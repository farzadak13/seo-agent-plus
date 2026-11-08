"""The latest scan of a site, and its two file changes. One record each, replaced in place."""
from __future__ import annotations

from app.models.persistence import PersistenceRecord
from app.models.site_files import SiteFileChange, SiteFileKind
from app.models.site_scan import ScanStatus, SiteScan
from app.persistence.contracts import Repository

SCAN_AGGREGATE_TYPE = "site_scan"
# The last scan that finished: what pages, files, words and descriptions are built
# from, untouched while a newer scan is queued, running, or has failed.
COMPLETED_SCAN_AGGREGATE_TYPE = "site_scan_completed"
FILE_AGGREGATE_TYPE = "site_file_change"
SCHEMA_VERSION = 1


class _Latest:
    """Keep one current record per aggregate: an old scan says nothing a new one doesn't."""

    def __init__(self, repository: Repository, aggregate_type: str) -> None:
        self._repository = repository
        self._type = aggregate_type

    def _read(self, aggregate_id: str):
        return self._repository.get(aggregate_type=self._type, aggregate_id=aggregate_id)

    def _write(self, aggregate_id: str, payload: dict, *, tenant_id: str, site_id: str) -> None:
        current = self._read(aggregate_id)
        version = (current.version + 1) if current else 1
        record = PersistenceRecord(
            record_id=f"{self._type}:{aggregate_id}:v{version}",
            aggregate_type=self._type,
            aggregate_id=aggregate_id,
            schema_version=SCHEMA_VERSION,
            version=version,
            payload=payload,
            tenant_id=tenant_id,
            site_id=site_id,
        )
        if current:
            self._repository.replace(record, expected_version=current.version)
        else:
            self._repository.create(record)


class SiteScanStore(_Latest):
    def __init__(self, repository: Repository) -> None:
        super().__init__(repository, SCAN_AGGREGATE_TYPE)

    def find(self, site_id: str) -> SiteScan | None:
        """The latest attempt, whatever its state."""
        record = self._read(site_id)
        return SiteScan.model_validate(record.payload) if record else None

    def completed(self, site_id: str) -> SiteScan | None:
        """The last scan that finished; None when none has."""
        record = self._repository.get(aggregate_type=COMPLETED_SCAN_AGGREGATE_TYPE, aggregate_id=site_id)
        if record is not None:
            return SiteScan.model_validate(record.payload)
        latest = self.find(site_id)  # kept before there was a record of its own
        return latest if latest is not None and latest.status == ScanStatus.COMPLETED else None

    def save(self, scan: SiteScan) -> SiteScan:
        # The fields only answers carry are never kept.
        payload = scan.model_dump(mode="json", exclude={"last_attempt_error", "monthly_rescan", "next_scan_at"})
        completed_store = _Latest(self._repository, COMPLETED_SCAN_AGGREGATE_TYPE)
        # A site scanned before there was a record of the last finished scan:
        # the finished scan about to be written over is kept first.
        if completed_store._read(scan.site_id) is None:
            current = self.find(scan.site_id)
            if current is not None and current.status == ScanStatus.COMPLETED and current.job_id != scan.job_id:
                completed_store._write(
                    scan.site_id, current.model_dump(mode="json"), tenant_id=current.tenant_id, site_id=current.site_id
                )
        self._write(scan.site_id, payload, tenant_id=scan.tenant_id, site_id=scan.site_id)
        if scan.status == ScanStatus.COMPLETED:
            completed_store._write(
                scan.site_id, payload, tenant_id=scan.tenant_id, site_id=scan.site_id
            )
        return scan

    def for_tenant(self, tenant_id: str) -> list[SiteScan]:
        """The latest scan of each of a tenant's sites, for its limits."""
        scans: list[SiteScan] = []
        cursor = None
        while True:
            page = self._repository.query(tenant_id=tenant_id, aggregate_type=self._type, limit=200, cursor=cursor)
            scans.extend(SiteScan.model_validate(record.payload) for record in page.records)
            if page.next_cursor is None:
                return scans
            cursor = page.next_cursor


class SiteFileStore(_Latest):
    def __init__(self, repository: Repository) -> None:
        super().__init__(repository, FILE_AGGREGATE_TYPE)

    def find(self, site_id: str, kind: SiteFileKind) -> SiteFileChange | None:
        record = self._read(f"{site_id}:{kind.value}")
        return SiteFileChange.model_validate(record.payload) if record else None

    def for_site(self, site_id: str) -> list[SiteFileChange]:
        found = [self.find(site_id, kind) for kind in SiteFileKind]
        return [change for change in found if change is not None]

    def save(self, change: SiteFileChange) -> SiteFileChange:
        self._write(
            f"{change.site_id}:{change.kind.value}",
            change.model_dump(mode="json"),
            tenant_id=change.tenant_id,
            site_id=change.site_id,
        )
        return change
