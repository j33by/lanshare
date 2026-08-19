import pytest

from lanshare.crypto import Cipher, DecryptionError, derive_key, generate_pin, generate_salt


def test_generate_pin_length_and_digits():
    pin = generate_pin(6)
    assert len(pin) == 6
    assert pin.isdigit()


def test_same_pin_and_salt_produce_same_key():
    salt = generate_salt()
    key1 = derive_key("123456", salt)
    key2 = derive_key("123456", salt)
    assert key1 == key2


def test_different_pins_produce_different_keys():
    salt = generate_salt()
    key1 = derive_key("111111", salt)
    key2 = derive_key("222222", salt)
    assert key1 != key2


def test_encrypt_decrypt_round_trip():
    key = derive_key("999999", generate_salt())
    cipher_send = Cipher(key)
    cipher_recv = Cipher(key)

    message = b"the quick brown fox jumps over the lazy dog"
    encrypted = cipher_send.encrypt(message)
    decrypted = cipher_recv.decrypt(encrypted)
    assert decrypted == message


def test_wrong_key_fails_to_decrypt():
    salt = generate_salt()
    key_a = derive_key("111111", salt)
    key_b = derive_key("222222", salt)

    encrypted = Cipher(key_a).encrypt(b"secret payload")
    with pytest.raises(DecryptionError):
        Cipher(key_b).decrypt(encrypted)
