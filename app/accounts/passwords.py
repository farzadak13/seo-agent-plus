"""Password hashing with scrypt, from the standard library.

scrypt is memory-hard, which is what makes guessing a stolen hash slow on the
hardware attackers use. The parameters travel with each hash, so they can be
raised later without breaking existing passwords: an old hash is verified
with its own parameters and replaced on the next successful sign-in.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2**14, 8, 1  # ~16 MiB per hash
KEY_LENGTH = 64
MIN_LENGTH = 10
MAX_LENGTH = 256  # bounds the work an attacker can make one request cost


class WeakPasswordError(ValueError):
    pass


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPasswordError(f"A password needs at least {MIN_LENGTH} characters.")
    if len(password) > MAX_LENGTH:
        raise WeakPasswordError(f"A password can have at most {MAX_LENGTH} characters.")


def hash_password(password: str) -> str:
    check_strength(password)
    salt = secrets.token_bytes(16)
    key = _derive(password, salt, N, R, P)
    return "$".join(
        ["scrypt", str(N), str(R), str(P), _b64(salt), _b64(key)]
    )


def verify_password(password: str, stored: str | None) -> bool:
    """True if it matches. Costs the same whether or not ``stored`` exists."""
    if not stored or len(password) > MAX_LENGTH:
        # Still do the work, so a missing account answers as slowly as a
        # wrong password and response time does not reveal who has one.
        _derive(password[:MAX_LENGTH] or "x", b"\0" * 16, N, R, P)
        return False
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.urlsafe_b64decode(key)
        actual = _derive(password, base64.urlsafe_b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    try:
        scheme, n, r, p, *_ = stored.split("$")
        return scheme != "scrypt" or (int(n), int(r), int(p)) != (N, R, P)
    except ValueError:
        return True


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=KEY_LENGTH
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii")
