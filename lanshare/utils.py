"""Small shared helpers used across the package."""

from __future__ import annotations

import hashlib
import socket
import sys
import time


def get_local_ip() -> str:
    """Best-effort guess at this machine's LAN IP address.

    Works whether the device is connected to a router's Wi-Fi network or to
    another device's mobile hotspot, since both simply present themselves
    as a normal local network interface.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't actually send any packets - just forces the OS to pick
        # the outbound interface it would use, which we then read back.
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def format_size(num_bytes: float) -> str:
    """Render a byte count as a human-readable string, e.g. '12.4 MB'."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_rate(bytes_per_sec: float) -> str:
    return f"{format_size(bytes_per_sec)}/s"


def sha256_of_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute the SHA-256 checksum of a file on disk."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


class ProgressBar:
    """A minimal, dependency-free progress indicator for the terminal."""

    def __init__(self, total: int, label: str = "", width: int = 32):
        self.total = max(total, 1)
        self.label = label
        self.width = width
        self.done = 0
        self._start = time.time()
        self._last_draw = 0.0

    def update(self, amount: int) -> None:
        self.done += amount
        now = time.time()
        # Redraw at most ~20 times per second to keep output smooth.
        if now - self._last_draw < 0.05 and self.done < self.total:
            return
        self._last_draw = now
        self._draw()

    def _draw(self) -> None:
        fraction = min(self.done / self.total, 1.0)
        filled = int(self.width * fraction)
        bar = "#" * filled + "-" * (self.width - filled)
        elapsed = max(time.time() - self._start, 1e-6)
        rate = self.done / elapsed
        sys.stdout.write(
            f"\r{self.label} [{bar}] {fraction * 100:5.1f}%  "
            f"{format_size(self.done)}/{format_size(self.total)}  "
            f"{format_rate(rate)}   "
        )
        sys.stdout.flush()

    def close(self) -> None:
        self.done = self.total
        self._draw()
        sys.stdout.write("\n")
        sys.stdout.flush()
