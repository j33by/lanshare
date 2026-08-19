import os
import tempfile

from lanshare.utils import format_size, sha256_of_file


def test_format_size_bytes():
    assert format_size(500) == "500.0 B"


def test_format_size_kb():
    assert format_size(2048) == "2.0 KB"


def test_format_size_mb():
    assert format_size(5 * 1024 * 1024) == "5.0 MB"


def test_sha256_of_file_matches_known_value():
    import hashlib

    content = b"hello world"
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(content)
        path = f.name
    try:
        assert sha256_of_file(path) == hashlib.sha256(content).hexdigest()
    finally:
        os.unlink(path)


def test_sha256_of_file_is_deterministic():
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"some file contents for hashing")
        path = f.name
    try:
        first = sha256_of_file(path)
        second = sha256_of_file(path)
        assert first == second
        assert len(first) == 64
    finally:
        os.unlink(path)
