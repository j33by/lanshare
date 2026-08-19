"""
Peer discovery over the local network.

A receiver periodically listens for a UDP broadcast asking "who's out
there?" and replies with its name, IP address, and the TCP port its
transfer service is listening on. A sender broadcasts that question once
and collects whatever answers come back within a short window.

This works both on a shared Wi-Fi network and when one device is
tethering to another's mobile hotspot, since in both cases every device
sits on the same local broadcast domain.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import List

from .utils import get_local_ip

DISCOVERY_PORT = 50555
BROADCAST_ADDR = "255.255.255.255"
MAGIC = "LANSHARE_DISCOVERY_V1"


@dataclass
class Peer:
    name: str
    ip: str
    port: int

    def __str__(self) -> str:
        return f"{self.name} ({self.ip}:{self.port})"


def _make_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    return sock


def discover_peers(timeout: float = 3.0) -> List[Peer]:
    """Broadcast a discovery request and collect replies for `timeout`
    seconds. Returns a de-duplicated list of peers that responded."""
    sock = _make_socket()
    sock.settimeout(0.5)
    request = json.dumps({"magic": MAGIC, "type": "REQUEST"}).encode("utf-8")

    peers = {}
    deadline = time.time() + timeout
    sock.sendto(request, (BROADCAST_ADDR, DISCOVERY_PORT))

    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if msg.get("magic") != MAGIC or msg.get("type") != "REPLY":
            continue
        ip = addr[0]
        peers[ip] = Peer(name=msg.get("name", "unknown"), ip=ip, port=msg.get("port", 0))

    sock.close()
    return list(peers.values())


class DiscoveryResponder:
    """Background service that answers discovery requests while a receiver
    is running, so senders can find it by name instead of typing an IP."""

    def __init__(self, name: str, service_port: int):
        self.name = name
        self.service_port = service_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        sock = _make_socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            # Port already taken by another lanshare instance on this host;
            # discovery simply won't be available, direct IP still works.
            return
        sock.settimeout(0.5)

        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if msg.get("magic") != MAGIC or msg.get("type") != "REQUEST":
                continue
            reply = json.dumps(
                {
                    "magic": MAGIC,
                    "type": "REPLY",
                    "name": self.name,
                    "port": self.service_port,
                    "ip": get_local_ip(),
                }
            ).encode("utf-8")
            sock.sendto(reply, addr)
        sock.close()
