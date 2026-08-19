import os
import tempfile
import threading
import time

from lanshare.transfer import ReceiverServer, send_file


def _free_port() -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_full_transfer_round_trip():
    port = _free_port()
    pin = "654321"

    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_path = os.path.join(src_dir, "sample.txt")
        payload = b"integration test payload " * 10000  # a few hundred KB
        with open(src_path, "wb") as f:
            f.write(payload)

        server = ReceiverServer(save_dir=dst_dir, pin=pin, port=port)
        server.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)  # let the listener come up

        try:
            send_file(src_path, host="127.0.0.1", pin=pin, port=port)
            time.sleep(0.3)  # let the receiver finish writing/verifying

            received_files = os.listdir(dst_dir)
            assert len(received_files) == 1
            with open(os.path.join(dst_dir, received_files[0]), "rb") as f:
                assert f.read() == payload
        finally:
            server.stop()


def test_wrong_pin_is_rejected():
    port = _free_port()

    with tempfile.TemporaryDirectory() as src_dir, tempfile.TemporaryDirectory() as dst_dir:
        src_path = os.path.join(src_dir, "sample.txt")
        with open(src_path, "wb") as f:
            f.write(b"top secret")

        server = ReceiverServer(save_dir=dst_dir, pin="111111", port=port)
        server.start()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)

        try:
            raised = False
            try:
                send_file(src_path, host="127.0.0.1", pin="999999", port=port)
            except Exception:
                raised = True
            assert raised
            assert os.listdir(dst_dir) == []
        finally:
            server.stop()
