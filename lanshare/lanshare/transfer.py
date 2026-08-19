"""
The actual file-transfer protocol.

Wire format, all on a single TCP connection:

    1. Client -> Server : 16 raw bytes            (PBKDF2 salt, cleartext)
    2. Client -> Server : encrypted frame          (JSON metadata)
    3. Server -> Client : encrypted frame          ({"status": "ready"})
    4. Client -> Server : encrypted frame(s)       (file contents, chunked)
    5. Server -> Client : encrypted frame          (final status)

Every "encrypted frame" is a 4-byte big-endian length prefix followed by
that many bytes of (12-byte AES-GCM nonce + ciphertext + 16-byte tag).
Nothing about the file - name, size, or contents - ever touches the wire
in plaintext.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from .crypto import Cipher, DecryptionError, derive_key, generate_salt
from .discovery import DiscoveryResponder
from .utils import ProgressBar, sha256_of_file

CHUNK_SIZE = 1 << 20  # 1 MiB per chunk keeps memory flat and throughput high
DEFAULT_PORT = 50556
LENGTH_HEADER = struct.Struct(">I")
MAX_FRAME_SIZE = CHUNK_SIZE + 4096  # generous headroom over one chunk + overhead


class TransferError(Exception):
    pass


# --------------------------------------------------------------------------
# low-level framing helpers
# --------------------------------------------------------------------------

def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise TransferError("Connection closed unexpectedly.")
        buf.extend(chunk)
    return bytes(buf)


def _send_frame(sock: socket.socket, cipher: Cipher, payload: bytes) -> None:
    blob = cipher.encrypt(payload)
    sock.sendall(LENGTH_HEADER.pack(len(blob)) + blob)


def _recv_frame(sock: socket.socket, cipher: Cipher) -> bytes:
    (length,) = LENGTH_HEADER.unpack(_recv_exact(sock, 4))
    if length > MAX_FRAME_SIZE:
        raise TransferError("Frame too large - refusing to read (possible corruption).")
    blob = _recv_exact(sock, length)
    return cipher.decrypt(blob)


def _send_json(sock: socket.socket, cipher: Cipher, obj: dict) -> None:
    _send_frame(sock, cipher, json.dumps(obj).encode("utf-8"))


def _recv_json(sock: socket.socket, cipher: Cipher) -> dict:
    return json.loads(_recv_frame(sock, cipher).decode("utf-8"))


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------

def send_file(
    path: str,
    host: str,
    pin: str,
    port: int = DEFAULT_PORT,
    sender_name: str = "lanshare",
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> None:
    """Connect to a receiver and transfer a single file, encrypted with `pin`."""
    if not os.path.isfile(path):
        raise TransferError(f"No such file: {path}")

    size = os.path.getsize(path)
    filename = os.path.basename(path)
    checksum = sha256_of_file(path)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(15)
    try:
        sock.connect((host, port))

        salt = generate_salt()
        sock.sendall(salt)
        key = derive_key(pin, salt)
        cipher = Cipher(key)

        _send_json(sock, cipher, {
            "filename": filename,
            "size": size,
            "checksum": checksum,
            "sender": sender_name,
        })

        reply = _recv_json(sock, cipher)
        if reply.get("status") != "ready":
            raise TransferError(reply.get("reason", "Receiver rejected the transfer."))

        sock.settimeout(60)
        bar = ProgressBar(size, label=f"Sending {filename}") if on_progress is None else None
        sent = 0
        with open(path, "rb") as f:
            while True:
                block = f.read(CHUNK_SIZE)
                if not block:
                    break
                _send_frame(sock, cipher, block)
                sent += len(block)
                if bar:
                    bar.update(len(block))
                if on_progress:
                    on_progress(sent, size)
        if bar:
            bar.close()

        final = _recv_json(sock, cipher)
        if final.get("status") != "success":
            raise TransferError(final.get("reason", "Transfer failed verification."))
    finally:
        sock.close()


# --------------------------------------------------------------------------
# receiving
# --------------------------------------------------------------------------

@dataclass
class ReceivedFile:
    path: str
    size: int
    sender: str


class ReceiverServer:
    """Accepts incoming transfers on a TCP port, one thread per connection.

    Also runs a small UDP responder in the background so senders can find
    this device by broadcast instead of needing to know its IP.
    """

    def __init__(
        self,
        save_dir: str,
        pin: str,
        port: int = DEFAULT_PORT,
        device_name: Optional[str] = None,
        on_file_received: Optional[Callable[[ReceivedFile], None]] = None,
        on_progress: Optional[Callable[[str, int, int], None]] = None,
    ):
        self.save_dir = save_dir
        self.pin = pin
        self.port = port
        self.device_name = device_name or socket.gethostname()
        self.on_file_received = on_file_received
        self.on_progress = on_progress

        os.makedirs(self.save_dir, exist_ok=True)

        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._responder = DiscoveryResponder(self.device_name, self.port)

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.port))
        self._sock.listen(5)
        self._responder.start()

    def serve_forever(self) -> None:
        if self._sock is None:
            self.start()
        self._sock.settimeout(0.5)
        try:
            while not self._stop.is_set():
                try:
                    conn, addr = self._sock.accept()
                except socket.timeout:
                    continue
                thread = threading.Thread(
                    target=self._handle_connection, args=(conn, addr), daemon=True
                )
                thread.start()
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop.set()
        self._responder.stop()
        if self._sock:
            self._sock.close()

    def _handle_connection(self, conn: socket.socket, addr) -> None:
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(30)
        try:
            salt = _recv_exact(conn, 16)
            key = derive_key(self.pin, salt)
            cipher = Cipher(key)

            try:
                meta = _recv_json(conn, cipher)
            except DecryptionError:
                # Wrong PIN. Say nothing further and drop the connection -
                # revealing anything here would help an attacker brute
                # force the PIN.
                return

            filename = os.path.basename(meta.get("filename", "received_file"))
            if not filename or filename in (".", ".."):
                filename = "received_file"
            size = int(meta.get("size", 0))
            expected_checksum = meta.get("checksum", "")
            sender = meta.get("sender", addr[0])

            dest_path = self._unique_path(filename)

            _send_json(conn, cipher, {"status": "ready"})

            received = 0
            conn.settimeout(60)
            with open(dest_path, "wb") as f:
                while received < size:
                    block = _recv_frame(conn, cipher)
                    f.write(block)
                    received += len(block)
                    if self.on_progress:
                        self.on_progress(filename, received, size)

            actual_checksum = sha256_of_file(dest_path)
            if expected_checksum and actual_checksum != expected_checksum:
                _send_json(conn, cipher, {
                    "status": "error",
                    "reason": "Checksum mismatch - file may be corrupted.",
                })
                return

            _send_json(conn, cipher, {"status": "success"})

            if self.on_file_received:
                self.on_file_received(ReceivedFile(path=dest_path, size=received, sender=sender))

        except (TransferError, DecryptionError, OSError, json.JSONDecodeError):
            # Any malformed or dropped connection is simply abandoned; a
            # single bad peer should never take the receiver down.
            pass
        finally:
            conn.close()

    def _unique_path(self, filename: str) -> str:
        """Avoid clobbering an existing file with the same name."""
        base, ext = os.path.splitext(filename)
        candidate = os.path.join(self.save_dir, filename)
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(self.save_dir, f"{base} ({counter}){ext}")
            counter += 1
        return candidate
