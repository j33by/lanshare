"""
Encryption for transfers.

Every transfer is protected by a short numeric PIN that the two people
agree on out-of-band (read aloud, typed in, sent over chat, etc.). The PIN
never goes over the network. Instead, both sides derive the same AES-256
key from the PIN using PBKDF2, and every chunk of the file is encrypted
with AES-GCM, which also guarantees the data wasn't tampered with in
transit.

If someone connects with the wrong PIN, the very first decryption attempt
fails and the connection is dropped before any file data is exchanged.
"""

from __future__ import annotations

import os
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

PBKDF2_ITERATIONS = 200_000
KEY_LENGTH = 32          # AES-256
SALT_LENGTH = 16
NONCE_LENGTH = 12


class DecryptionError(Exception):
    """Raised when a chunk fails to authenticate (wrong PIN or corruption)."""


def generate_pin(digits: int = 6) -> str:
    """Generate a random numeric PIN, e.g. '482913'."""
    return "".join(secrets.choice("0123456789") for _ in range(digits))


def generate_salt() -> bytes:
    return os.urandom(SALT_LENGTH)


def derive_key(pin: str, salt: bytes) -> bytes:
    """Derive a 256-bit key from a PIN and salt. The salt may be public;
    the PIN provides the actual secrecy."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(pin.encode("utf-8"))


class Cipher:
    """Thin wrapper around AES-GCM that handles nonce bookkeeping."""

    def __init__(self, key: bytes):
        self._aead = AESGCM(key)
        # A monotonically increasing counter guarantees each nonce is
        # unique for the lifetime of a single connection, which is a
        # strict requirement for AES-GCM safety.
        self._send_counter = 0

    def encrypt(self, plaintext: bytes) -> bytes:
        nonce = self._send_counter.to_bytes(NONCE_LENGTH, "big")
        self._send_counter += 1
        ciphertext = self._aead.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt(self, data: bytes) -> bytes:
        nonce, ciphertext = data[:NONCE_LENGTH], data[NONCE_LENGTH:]
        try:
            return self._aead.decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            raise DecryptionError(
                "Could not authenticate data - the PIN is likely incorrect."
            ) from exc
