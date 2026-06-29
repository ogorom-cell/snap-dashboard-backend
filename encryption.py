from __future__ import annotations
"""
AES-256-CBC encryption for Snap media uploads.
Snap requires all media to be encrypted before sending to their API.
"""
import base64
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


def encrypt_media(raw_bytes: bytes) -> tuple[bytes, str, str]:
    """
    Encrypt raw media bytes with AES-256-CBC.

    Returns:
        (encrypted_bytes, key_base64, iv_base64)

    Both key_base64 and iv_base64 must be sent alongside the encrypted
    file in the Snap media upload API request.
    """
    key = os.urandom(32)   # 256-bit key
    iv = os.urandom(16)    # 128-bit IV

    # PKCS7 padding to make length a multiple of 16
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw_bytes) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    return (
        encrypted,
        base64.b64encode(key).decode(),
        base64.b64encode(iv).decode(),
    )


def decrypt_media(encrypted_bytes: bytes, key_b64: str, iv_b64: str) -> bytes:
    """Decrypt AES-256-CBC bytes — useful for testing/verification."""
    key = base64.b64decode(key_b64)
    iv = base64.b64decode(iv_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(encrypted_bytes) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()
