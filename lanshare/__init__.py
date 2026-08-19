"""
lanshare
~~~~~~~~

Peer-to-peer file transfer over a local network (Wi-Fi or mobile hotspot).

No internet connection, cloud relay, or Bluetooth pairing is required.
Two devices on the same network segment can find each other and exchange
files directly, with the transfer encrypted end-to-end using a short-lived
PIN.
"""

__version__ = "1.0.0"
__author__ = "lanshare contributors"

from .transfer import send_file, ReceiverServer
from .discovery import discover_peers

__all__ = ["send_file", "ReceiverServer", "discover_peers", "__version__"]
