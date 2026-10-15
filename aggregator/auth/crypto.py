"""Encryption, keyed hashing and password hashing for account data.

- Personal fields are sealed with AES-256-GCM (DATA_KEY). Every value is bound
  to where it lives through associated data ("users.email:<user id>"), so a
  ciphertext copied into another row or column fails to decrypt.
  Layout: 1 version byte | 12-byte random nonce | ciphertext+tag.
- Lookups that must work without decrypting (email, OAuth subject) and stored
  secrets that are only ever compared (session tokens, backup codes) use
  HMAC-SHA256 with a separate key (INDEX_KEY).
- Passwords: Argon2id.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings

_VERSION = b"\x01"
# Argon2id, ~64 MiB memory: OWASP's recommended class of parameters.
_ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, salt_len=16)
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))


def _aead() -> AESGCM:
    return AESGCM(settings.data_key)


def seal(value: str | bytes | None, where: str) -> bytes | None:
    if value is None:
        return None
    data = value.encode() if isinstance(value, str) else value
    nonce = os.urandom(12)
    return _VERSION + nonce + _aead().encrypt(nonce, data, where.encode())


def open_(blob: bytes | None, where: str) -> str | None:
    if blob is None:
        return None
    if blob[:1] != _VERSION:
        raise ValueError("unknown ciphertext version")
    return _aead().decrypt(blob[1:13], blob[13:], where.encode()).decode()


def seal_json(obj, where: str) -> bytes:
    return seal(json.dumps(obj, separators=(",", ":")), where)


def open_json(blob: bytes | None, where: str):
    s = open_(blob, where)
    return json.loads(s) if s is not None else None


def index(purpose: str, value: str) -> bytes:
    """Deterministic keyed hash for lookups (e.g. email -> user) and comparisons."""
    return hmac.new(settings.index_key, f"{purpose}\x00{value}".encode(), hashlib.sha256).digest()


def token() -> str:
    return secrets.token_urlsafe(32)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(stored: str | None, password: str) -> tuple[bool, str | None]:
    """Returns (ok, new_hash_if_parameters_changed). Always does the work, even for
    accounts without a password, so timing doesn't reveal which emails exist."""
    try:
        _ph.verify(stored or _DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if stored is None:
        return False, None
    return True, (_ph.hash(password) if _ph.check_needs_rehash(stored) else None)


def norm_email(email: str) -> str:
    return email.strip().lower()
