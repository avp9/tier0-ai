from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MAX_PASSWORD_CHARS = 256

_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("tier0-timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def spend_verification_time(password: str) -> None:
    """Do the same work as a real check, so an unknown account takes as long as a wrong password."""
    verify_password(_DUMMY_HASH, password)
