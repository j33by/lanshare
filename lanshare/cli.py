"""Command-line entry point for lanshare."""

from __future__ import annotations

import argparse
import os
import socket
import sys

from . import __version__
from .crypto import generate_pin
from .discovery import discover_peers
from .transfer import DEFAULT_PORT, ReceiverServer, TransferError, send_file
from .utils import format_size, get_local_ip


def _cmd_receive(args: argparse.Namespace) -> int:
    pin = args.pin or generate_pin()
    save_dir = os.path.abspath(args.out)
    device_name = args.name or socket.gethostname()

    def on_received(f):
        print(f"\nSaved: {f.path}  ({format_size(f.size)}, from {f.sender})")

    server = ReceiverServer(
        save_dir=save_dir,
        pin=pin,
        port=args.port,
        device_name=device_name,
        on_file_received=on_received,
    )
    server.start()

    print(f"lanshare receiver running as '{device_name}'")
    print(f"  Listening on : {get_local_ip()}:{args.port}")
    print(f"  Saving to    : {save_dir}")
    print(f"  PIN          : {pin}")
    print("\nShare the PIN with the sender. Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        server.stop()
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    host = args.host
    if not host:
        print("No --host given, searching the network for receivers...")
        peers = discover_peers(timeout=args.timeout)
        if not peers:
            print("No receivers found. Make sure the other device is running "
                  "'lanshare receive' and you're both on the same network "
                  "or hotspot, then try again with --host <ip>.")
            return 1
        if len(peers) == 1:
            host, port = peers[0].ip, peers[0].port
            print(f"Found {peers[0]}")
        else:
            print("Multiple receivers found:")
            for i, p in enumerate(peers, 1):
                print(f"  {i}. {p}")
            choice = input(f"Pick one [1-{len(peers)}]: ").strip()
            try:
                idx = int(choice) - 1
                host, port = peers[idx].ip, peers[idx].port
            except (ValueError, IndexError):
                print("Invalid selection.")
                return 1
        args.port = port

    pin = args.pin
    if not pin:
        pin = input("Enter the PIN shown on the receiving device: ").strip()

    sender_name = args.name or socket.gethostname()

    for path in args.files:
        try:
            send_file(path, host=host, pin=pin, port=args.port, sender_name=sender_name)
            print(f"Done: {path}")
        except TransferError as exc:
            print(f"Failed to send {path}: {exc}")
            return 1
        except (ConnectionRefusedError, OSError) as exc:
            print(f"Could not reach {host}:{args.port} - {exc}")
            return 1
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    try:
        from .gui import main as gui_main
    except SystemExit as exc:
        print(exc)
        return 1
    gui_main()
    return 0


def _cmd_discover(args: argparse.Namespace) -> int:
    print(f"Searching for receivers for {args.timeout:.0f}s...")
    peers = discover_peers(timeout=args.timeout)
    if not peers:
        print("No receivers found on this network.")
        return 0
    print(f"Found {len(peers)} receiver(s):")
    for p in peers:
        print(f"  - {p}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lanshare",
        description=(
            "Send files directly between devices on the same Wi-Fi network "
            "or mobile hotspot - no internet, cloud account, or Bluetooth "
            "pairing required."
        ),
    )
    parser.add_argument("--version", action="version", version=f"lanshare {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_recv = sub.add_parser("receive", help="Wait to receive incoming files")
    p_recv.add_argument("-o", "--out", default="./received", help="Directory to save files into")
    p_recv.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    p_recv.add_argument("--pin", help="Use a specific PIN instead of a random one")
    p_recv.add_argument("--name", help="Name to advertise to senders")
    p_recv.set_defaults(func=_cmd_receive)

    p_send = sub.add_parser("send", help="Send one or more files to a receiver")
    p_send.add_argument("files", nargs="+", help="Path(s) of file(s) to send")
    p_send.add_argument("--host", help="IP address of the receiver (skip auto-discovery)")
    p_send.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help="Receiver's port")
    p_send.add_argument("--pin", help="Receiver's PIN (will prompt if omitted)")
    p_send.add_argument("--name", help="Name to identify yourself as")
    p_send.add_argument("--timeout", type=float, default=3.0, help="Discovery timeout in seconds")
    p_send.set_defaults(func=_cmd_send)

    p_disc = sub.add_parser("discover", help="List receivers currently available on the network")
    p_disc.add_argument("--timeout", type=float, default=3.0, help="How long to listen for replies")
    p_disc.set_defaults(func=_cmd_discover)

    p_gui = sub.add_parser("gui", help="Launch the desktop GUI")
    p_gui.set_defaults(func=_cmd_gui)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
