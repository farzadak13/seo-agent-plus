"""Persistence for tenants and their keys.

Both live in the same append-only repository as everything else, so a key's
history — issued, revoked — is kept rather than overwritten. Knowing when a key
was revoked is the difference between answering "was this request legitimate at
the time" and guessing.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.persistence import PersistenceRecord
from app.models.tenants import APIKeyRecord, Tenant
from app.persistence.contracts import PersistenceNotFoundError, Repository

TENANT_AGGREGATE_TYPE = "tenant"
API_KEY_AGGREGATE_TYPE = "api_key"
SCHEMA_VERSION = 1


class TenantStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, tenant: Tenant) -> Tenant:
        self._repository.create(self._to_record(tenant, version=1))
        return tenant

    def get(self, tenant_id: str) -> Tenant:
        record = self._repository.get(
            aggregate_type=TENANT_AGGREGATE_TYPE, aggregate_id=tenant_id
        )
        if record is None:
            raise PersistenceNotFoundError(f"tenant not found: {tenant_id}")
        return Tenant.model_validate(record.payload)

    def find(self, tenant_id: str) -> Tenant | None:
        try:
            return self.get(tenant_id)
        except PersistenceNotFoundError:
            return None

    def update(self, tenant: Tenant) -> Tenant:
        current = self._repository.get(
            aggregate_type=TENANT_AGGREGATE_TYPE, aggregate_id=tenant.tenant_id
        )
        if current is None:
            raise PersistenceNotFoundError(f"tenant not found: {tenant.tenant_id}")
        updated = tenant.model_copy(update={"updated_at": datetime.now(timezone.utc)})
        self._repository.replace(
            self._to_record(updated, version=current.version + 1),
            expected_version=current.version,
        )
        return updated

    def list_all(self) -> list[Tenant]:
        return [
            Tenant.model_validate(record.payload)
            for record in self._repository.list(aggregate_type=TENANT_AGGREGATE_TYPE)
        ]

    @staticmethod
    def _to_record(tenant: Tenant, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"tenant:{tenant.tenant_id}:v{version}",
            aggregate_type=TENANT_AGGREGATE_TYPE,
            aggregate_id=tenant.tenant_id,
            schema_version=SCHEMA_VERSION,
            version=version,
            payload=tenant.model_dump(mode="json"),
            tenant_id=tenant.tenant_id,
        )


class APIKeyStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, key: APIKeyRecord) -> APIKeyRecord:
        self._repository.create(self._to_record(key, version=1))
        return key

    def find(self, key_id: str) -> APIKeyRecord | None:
        """Look up by the public prefix. One indexed read, no scan."""
        record = self._repository.get(
            aggregate_type=API_KEY_AGGREGATE_TYPE, aggregate_id=key_id
        )
        if record is None:
            return None
        return APIKeyRecord.model_validate(record.payload)

    def revoke(self, key_id: str) -> APIKeyRecord:
        current = self._repository.get(
            aggregate_type=API_KEY_AGGREGATE_TYPE, aggregate_id=key_id
        )
        if current is None:
            raise PersistenceNotFoundError(f"api key not found: {key_id}")
        existing = APIKeyRecord.model_validate(current.payload)
        if not existing.active:
            return existing
        revoked = existing.model_copy(update={"revoked_at": datetime.now(timezone.utc)})
        self._repository.replace(
            self._to_record(revoked, version=current.version + 1),
            expected_version=current.version,
        )
        return revoked

    def list_for_tenant(self, tenant_id: str) -> list[APIKeyRecord]:
        keys = [
            APIKeyRecord.model_validate(record.payload)
            for record in self._repository.list(aggregate_type=API_KEY_AGGREGATE_TYPE)
        ]
        return [key for key in keys if key.tenant_id == tenant_id]

    @staticmethod
    def _to_record(key: APIKeyRecord, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"api-key:{key.key_id}:v{version}",
            aggregate_type=API_KEY_AGGREGATE_TYPE,
            aggregate_id=key.key_id,
            schema_version=SCHEMA_VERSION,
            version=version,
            payload=key.model_dump(mode="json"),
            tenant_id=key.tenant_id,
        )


__all__ = [
    "API_KEY_AGGREGATE_TYPE",
    "APIKeyStore",
    "TENANT_AGGREGATE_TYPE",
    "TenantStore",
]
