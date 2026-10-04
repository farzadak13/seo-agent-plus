"""Per-site credentials, encrypted in the database.

Until now a customer's WordPress password had to be added to the server's
environment file and the service restarted: fine for one site, impossible to
sell. The vault stores each value encrypted with AES-256-GCM under a key that
lives only in the environment (``SEO_AGENT_SECRET_KEYS``), so a database dump
or backup holds nothing usable on its own.

Each ciphertext is bound to its tenant, site and field as associated data. A
value copied from one site's record into another's, or from one field to
another, fails to decrypt rather than quietly authenticating as someone else.

Rotation: put the new key first and keep the old one after it. New writes use
the first key; reads find the right one by its id.
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import datetime, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field

from app.models.persistence import PersistenceRecord
from app.models.sites import SecretProvider, SecretRef
from app.onboarding.secrets import SecretResolutionError
from app.persistence.contracts import Repository


SECRET_AGGREGATE_TYPE = "site_secret"
SECRET_SCHEMA_VERSION = 1
FORMAT = "v1"


class VaultConfigurationError(RuntimeError):
    pass


class StoredSecret(BaseModel):
    """What is persisted. Never the value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=100)
    # Empty once erased. Earlier versions stay in the append-only store, so
    # erasing is not deletion: a credential that must stop working is revoked
    # where it was issued (see GoogleOAuthService.disconnect).
    ciphertext: str = Field(default="", repr=False)
    erased: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


def generate_key() -> str:
    """A fresh key in the form SEO_AGENT_SECRET_KEYS expects."""
    return base64.urlsafe_b64encode(AESGCM.generate_key(bit_length=256)).decode("ascii")


def reference_for(site_id: str, name: str) -> SecretRef:
    return SecretRef(provider=SecretProvider.DATABASE, key=f"{site_id}/{name}")


class Keyring:
    def __init__(self, encoded_keys: list[str]) -> None:
        keys = []
        for encoded in encoded_keys:
            try:
                raw = base64.urlsafe_b64decode(encoded.strip().encode("ascii"))
            except Exception as exc:
                raise VaultConfigurationError("A secret key is not valid base64.") from exc
            if len(raw) != 32:
                raise VaultConfigurationError("Each secret key must be 32 bytes (base64 of 256 bits).")
            keys.append(raw)
        if not keys:
            raise VaultConfigurationError("At least one secret key is required.")
        self._keys = {self._key_id(raw): raw for raw in keys}
        self._current = self._key_id(keys[0])

    @classmethod
    def from_environment(cls, name: str = "SEO_AGENT_SECRET_KEYS") -> "Keyring | None":
        value = os.getenv(name, "").strip()
        if not value:
            return None
        return cls([part for part in value.split(",") if part.strip()])

    @staticmethod
    def _key_id(raw: bytes) -> str:
        # Identifies a key without revealing it: 8 hex chars of its hash.
        return hashlib.sha256(raw).hexdigest()[:8]

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(12)
        sealed = AESGCM(self._keys[self._current]).encrypt(
            nonce, plaintext.encode("utf-8"), context.encode("utf-8")
        )
        body = base64.urlsafe_b64encode(nonce + sealed).decode("ascii")
        return f"{FORMAT}:{self._current}:{body}"

    def decrypt(self, token: str, *, context: str) -> str:
        try:
            version, key_id, body = token.split(":", 2)
        except ValueError:
            raise SecretResolutionError("Stored secret is malformed.") from None
        if version != FORMAT:
            raise SecretResolutionError(f"Unsupported stored secret format: {version}")
        key = self._keys.get(key_id)
        if key is None:
            raise SecretResolutionError(
                "Stored secret was encrypted with a key that is no longer configured."
            )
        blob = base64.urlsafe_b64decode(body.encode("ascii"))
        try:
            plaintext = AESGCM(key).decrypt(blob[:12], blob[12:], context.encode("utf-8"))
        except InvalidTag:
            raise SecretResolutionError(
                "Stored secret failed authentication; it was altered or moved."
            ) from None
        return plaintext.decode("utf-8")


class SecretVault:
    def __init__(self, repository: Repository, keyring: Keyring) -> None:
        self._repository = repository
        self._keyring = keyring

    def put(self, *, tenant_id: str, site_id: str, name: str, value: str) -> SecretRef:
        if not value.strip():
            raise ValueError(f"The value for {name} must not be empty.")
        stored = StoredSecret(
            tenant_id=tenant_id,
            site_id=site_id,
            name=name,
            ciphertext=self._keyring.encrypt(
                value, context=_context(tenant_id, site_id, name)
            ),
        )
        aggregate_id = f"{site_id}/{name}"
        current = self._repository.get(
            aggregate_type=SECRET_AGGREGATE_TYPE, aggregate_id=aggregate_id
        )
        version = 1 if current is None else current.version + 1
        record = PersistenceRecord(
            record_id=f"secret:{aggregate_id}:v{version}",
            aggregate_type=SECRET_AGGREGATE_TYPE,
            aggregate_id=aggregate_id,
            schema_version=SECRET_SCHEMA_VERSION,
            version=version,
            payload=stored.model_dump(mode="json"),
            tenant_id=tenant_id,
            site_id=site_id,
        )
        if current is None:
            self._repository.create(record)
        else:
            self._repository.replace(record, expected_version=current.version)
        return reference_for(site_id, name)

    def erase(self, aggregate_id: str) -> None:
        """Make the current value unreadable through the vault from now on."""
        current = self._repository.get(
            aggregate_type=SECRET_AGGREGATE_TYPE, aggregate_id=aggregate_id
        )
        if current is None:
            return
        stored = StoredSecret.model_validate(current.payload)
        tombstone = stored.model_copy(
            update={"ciphertext": "", "erased": True, "updated_at": datetime.now(timezone.utc)}
        )
        version = current.version + 1
        self._repository.replace(
            current.model_copy(
                update={
                    "record_id": f"secret:{aggregate_id}:v{version}",
                    "version": version,
                    "payload": tombstone.model_dump(mode="json"),
                }
            ),
            expected_version=current.version,
        )

    def get(self, aggregate_id: str) -> str:
        record = self._repository.get(
            aggregate_type=SECRET_AGGREGATE_TYPE, aggregate_id=aggregate_id
        )
        if record is None:
            raise SecretResolutionError(f"Secret is not configured: {aggregate_id}")
        stored = StoredSecret.model_validate(record.payload)
        if stored.erased:
            raise SecretResolutionError(f"This credential was disconnected: {aggregate_id}")
        # The context comes from where the value was looked up, not from the
        # payload beside it: a payload copied wholesale into another site's
        # slot would otherwise carry its own matching context along.
        site_id, _, name = aggregate_id.partition("/")
        return self._keyring.decrypt(
            stored.ciphertext,
            context=_context(record.tenant_id or "", site_id, name),
        )


class CompositeSecretResolver:
    """Environment references as before; database references through the vault."""

    def __init__(self, environment, vault: SecretVault | None) -> None:
        self._environment = environment
        self._vault = vault

    def resolve(self, reference: SecretRef) -> str:
        if reference.provider == SecretProvider.DATABASE:
            if self._vault is None:
                raise SecretResolutionError(
                    "This secret is stored in the database, but SEO_AGENT_SECRET_KEYS is not set."
                )
            return self._vault.get(reference.key)
        return self._environment.resolve(reference)


def _context(tenant_id: str, site_id: str, name: str) -> str:
    return f"{FORMAT}|{tenant_id}|{site_id}|{name}"
