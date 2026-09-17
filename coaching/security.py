from dataclasses import dataclass
import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

hasher = PasswordHasher()  # Argon2id; random salt and parameters embedded in each hash.
_DUMMY_HASH = hasher.hash("unused-timing-equalization-password")


@dataclass(frozen=True)
class Principal:
    user_id: int
    session_version: int


class AccessDenied(Exception):
    pass


def normalize_username(username: str) -> str:
    return username.strip().lower()


def validate_username(username: str) -> str:
    username = normalize_username(username)
    if not re.fullmatch(r"[a-z0-9_.-]{3,64}", username):
        raise ValueError("Use 3–64 letters, numbers, periods, underscores, or hyphens for the username.")
    return username


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 128:
        raise ValueError("Passwords must be 12–128 characters long.")
    return hasher.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    try:
        # Limit work and never log plaintext or verification exceptions.
        valid = hasher.verify(encoded or _DUMMY_HASH, password[:129])
        return bool(encoded) and valid and len(password) <= 128
    except (VerificationError, InvalidHashError):
        return False
