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


class TenantAPIKeyAuthenticator:
    """Resolves a bearer key to the tenant it belongs to.

    Replaces the single-key authenticator above for customer traffic. That one
    returned a constant principal id, which meant every ownership check on
    every endpoint compared a constant against itself and passed. The checks
    were written correctly and enforced nothing.

    **No key can see every tenant.** The configured ``SEO_AGENT_API_KEY``
    authenticates as the reserved principal ``admin``, and ``admin`` is
    treated as an ordinary tenant: it can hold its own sites and runs, and the
    ownership checks refuse it everyone else's. It is a bootstrap identity for
    operating the service, not a master key.

    That is weaker than it should eventually be — ``admin`` ought to be
    confined to tenant administration once there are endpoints for it — but
    the property that matters today holds: possessing any one key grants
    access to exactly one tenant's rows.

    **Revocation is not cached.** Every request costs one indexed lookup by the
    key's public prefix. Caching would buy a round trip and sell the property
    that a revoked key stops working immediately, which is the only reason
    revocation exists.
    """

    ADMIN_PRINCIPAL = "admin"

    def __init__(
        self,
        *,
        key_store,
        tenant_store=None,
        admin_key: str | None = None,
    ) -> None:
        self._key_store = key_store
        self._tenant_store = tenant_store
        self._admin = (
            APIKeyAuthenticator(admin_key, principal_id=self.ADMIN_PRINCIPAL)
            if admin_key
            else None
        )
        self._scheme = HTTPBearer(auto_error=False)

    @property
    def scheme(self) -> HTTPBearer:
        return self._scheme

    def authenticate(
        self,
        credentials: HTTPAuthorizationCredentials | None,
    ) -> str:
        if credentials is None:
            raise self._unauthorized("Authentication required.")
        if credentials.scheme.lower() != "bearer":
            raise self._unauthorized("Invalid authentication scheme.")

        from app.tenancy import keys as key_format

        token = credentials.credentials
        if not key_format.looks_like_ours(token):
            # Not one of ours in shape. The admin key predates the format and
            # is the only thing that can still be presented this way.
            if self._admin is not None:
                return self._admin.authenticate(credentials)
            raise self._unauthorized("Invalid credentials.")

        key_id, secret = key_format.parse(token)
        record = self._key_store.find(key_id)
        # Every rejection below is the same message on purpose: which of these
        # failed is information about someone else's key.
        if record is None or not record.active:
            raise self._unauthorized("Invalid credentials.")
        if not key_format.matches(
            key_id=key_id, secret=secret, expected_digest=record.digest
        ):
            raise self._unauthorized("Invalid credentials.")

        if self._tenant_store is not None:
            tenant = self._tenant_store.find(record.tenant_id)
            if tenant is None:
                raise self._unauthorized("Invalid credentials.")
            if tenant.status != "active":
                # Distinguishable on purpose: the key is real and the customer
                # can act on this, unlike the cases above.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This account is suspended.",
                )

        return record.tenant_id

    @staticmethod
    def _unauthorized(detail: str) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
