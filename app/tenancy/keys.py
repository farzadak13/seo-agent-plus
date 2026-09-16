"""Issuing and checking API keys.

The shape is ``seo_<key_id>_<secret>``.

The ``key_id`` prefix is the point. Without it, checking a key means comparing
it against the stored hash of every key in the database — every request, once
per customer. With it, the record is found by a single indexed read and exactly
one hash is computed.

The prefix is not a secret. It appears in logs and support tickets on purpose:
it identifies which key is being used without revealing anything that would let
someone use it.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

PREFIX = "seo"
SEPARATOR = "_"
KEY_ID_BYTES = 8
SECRET_BYTES = 32


class InvalidKeyFormat(ValueError):
    """The presented string is not shaped like one of our keys at all."""


@dataclass(frozen=True)
class IssuedKey:
    """A freshly minted key. The plaintext exists only here, once."""

    key_id: str
    secret: str
    digest: str

    @property
    def token(self) -> str:
        return f"{PREFIX}{SEPARATOR}{self.key_id}{SEPARATOR}{self.secret}"


def digest_for(key_id: str, secret: str) -> str:
    """Hash a secret, bound to its own key id.

    Including the key id means a stored digest is only ever valid for the
    record it belongs to, so a digest copied between rows authenticates
    nothing.
    """
    material = f"{key_id}{SEPARATOR}{secret}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def issue() -> IssuedKey:
    key_id = secrets.token_hex(KEY_ID_BYTES)
    # urlsafe so the key survives a shell, a URL and a config file unchanged.
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return IssuedKey(key_id=key_id, secret=secret, digest=digest_for(key_id, secret))


def parse(token: str) -> tuple[str, str]:
    """Split a presented key into (key_id, secret).

    ``token_urlsafe`` can emit ``_``, so the split is bounded at two: anything
    after the second separator belongs to the secret.
    """
    if not isinstance(token, str):
        raise InvalidKeyFormat("not a string")
    parts = token.strip().split(SEPARATOR, 2)
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1] or not parts[2]:
        raise InvalidKeyFormat("expected seo_<key_id>_<secret>")
    return parts[1], parts[2]


def looks_like_ours(token: str) -> bool:
    try:
        parse(token)
    except InvalidKeyFormat:
        return False
    return True


def matches(*, key_id: str, secret: str, expected_digest: str) -> bool:
    return hmac.compare_digest(digest_for(key_id, secret), expected_digest)


__all__ = [
    "InvalidKeyFormat",
    "IssuedKey",
    "PREFIX",
    "digest_for",
    "issue",
    "looks_like_ours",
    "matches",
    "parse",
]
