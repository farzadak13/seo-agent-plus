from __future__ import annotations

from datetime import datetime, timezone

from app.models.persistence import PersistenceRecord
from app.models.sites import Site
from app.persistence.contracts import PersistenceNotFoundError, Repository


SITE_AGGREGATE_TYPE = "site"
SITE_SCHEMA_VERSION = 1


class SiteStore:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def create(self, site: Site) -> Site:
        record = self._to_record(site, version=1)
        self._repository.create(record)
        return site

    def get(self, site_id: str) -> Site:
        record = self._repository.get(
            aggregate_type=SITE_AGGREGATE_TYPE,
            aggregate_id=site_id,
        )
        if record is None:
            raise PersistenceNotFoundError(f"site not found: {site_id}")
        return Site.model_validate(record.payload)

    def update(self, site: Site) -> Site:
        current = self._repository.get(
            aggregate_type=SITE_AGGREGATE_TYPE,
            aggregate_id=site.site_id,
        )
        if current is None:
            raise PersistenceNotFoundError(
                f"site not found: {site.site_id}"
            )
        updated = site.model_copy(
            update={"updated_at": datetime.now(timezone.utc)}
        )
        self._repository.replace(
            self._to_record(updated, version=current.version + 1),
            expected_version=current.version,
        )
        return updated

    @staticmethod
    def _to_record(site: Site, *, version: int) -> PersistenceRecord:
        return PersistenceRecord(
            record_id=f"site:{site.site_id}:v{version}",
            aggregate_type=SITE_AGGREGATE_TYPE,
            aggregate_id=site.site_id,
            schema_version=SITE_SCHEMA_VERSION,
            version=version,
            payload=site.model_dump(mode="json"),
            tenant_id=site.principal_id,
            site_id=site.site_id,
        )
