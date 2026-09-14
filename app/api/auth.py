from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


@dataclass(frozen=True)
class HashedAPIKey:
    salt: bytes
    digest: bytes
    iterations: int


class APIKeyAuthenticator:
    """
    Bearer API-key authentication.

    The plaintext key is accepted only by the constructor and is immediately
    converted into a PBKDF2 digest. The raw key is never retained by the
    authenticator.
    """

    def __init__(
        self,
        expected_key: str,
        *,
        principal_id: str = "api-key",
        iterations: int = 310_000,
    ) -> None:
        if not expected_key or not expected_key.strip():
            raise ValueError("expected_key cannot be empty")
        if iterations < 100_000:
            raise ValueError("iterations must be at least 100000")

        salt = secrets.token_bytes(16)
        digest = self._derive(
            expected_key.encode("utf-8"),
            salt,
            iterations,
        )
        self._credential = HashedAPIKey(
            salt=salt,
            digest=digest,
            iterations=iterations,
        )
        self._principal_id = principal_id
        self._scheme = HTTPBearer(auto_error=False)

    @property
    def scheme(self) -> HTTPBearer:
        return self._scheme

    def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None,
    ) -> str:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        supplied = credentials.credentials.encode("utf-8")
        digest = self._derive(
            supplied,
            self._credential.salt,
            self._credential.iterations,
        )

        if not hmac.compare_digest(
            digest,
            self._credential.digest,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return self._principal_id

    @staticmethod
    def _derive(
        value: bytes,
        salt: bytes,
        iterations: int,
    ) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            value,
            salt,
            iterations,
        )
