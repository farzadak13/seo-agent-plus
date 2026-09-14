"""Every version is its own row, so every version needs its own record_id.

migrations/001 declares UNIQUE (record_id). A store that reuses one record_id
across versions cannot write a second version to PostgreSQL at all — and that
is invisible in tests unless the in-memory double enforces the same rule, which
is how this went unnoticed until the first real database appeared.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.models.jobs import Job
from app.models.persistence import PersistenceRecord
from app.models.runs import SEORun
from app.models.sites import Site
from app.models.snapshots import SnapshotMetadata
from app.persistence.aggregates import build_run_record
from app.persistence.contracts import PersistenceConflictError
from app.persistence.memory import InMemoryRepository


SNAPSHOT = SnapshotMetadata(
    snapshot_id="snapshot-1",
    data_snapshot_id="data-1",
    rule_version="rules-v1",
    config_version="config-v1",
    generated_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
)


def site():
    return Site(site_id="site-1", principal_id="tenant-1", name="T", base_url="https://e.test/")


def run():
    return SEORun(
        run_id="run-1", site_id="site-1", principal_id="tenant-1", job_id="job-1",
        start_date=date(2026, 9, 1).isoformat(), end_date=date(2026, 9, 2).isoformat(),
        normalized_url="https://e.test/p", normalized_query="کفش", candidate_id="c-1",
    )


def job():
    return Job(job_id="job-1", job_type="seo_run", principal_id="tenant-1", payload={})


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(lambda v: __import__("app.onboarding.site_store", fromlist=["SiteStore"]).SiteStore._to_record(site(), version=v), id="site"),
        pytest.param(lambda v: __import__("app.runs.store", fromlist=["RunStore"]).RunStore._to_record(run(), version=v), id="run"),
        pytest.param(lambda v: __import__("app.jobs.store", fromlist=["JobStore"]).JobStore._to_record(job(), version=v), id="job"),
        pytest.param(lambda v: build_run_record(run_id="run-1", site_id="site-1", snapshot=SNAPSHOT, version=v), id="pipeline_run"),
    ],
)
def test_each_version_gets_its_own_record_id(build):
    assert build(1).record_id != build(2).record_id


def test_the_in_memory_double_rejects_a_reused_record_id():
    """The double must forbid what the schema forbids, or it hides the bug."""
    repository = InMemoryRepository()
    first = PersistenceRecord(
        record_id="shared", aggregate_type="t", aggregate_id="a", version=1, payload={}
    )
    second = PersistenceRecord(
        record_id="shared", aggregate_type="t", aggregate_id="a", version=2, payload={}
    )
    repository.create(first)

    with pytest.raises(PersistenceConflictError, match="record_id"):
        repository.replace(second, expected_version=1)


def test_a_reused_record_id_across_aggregates_is_also_rejected():
    repository = InMemoryRepository()
    repository.create(
        PersistenceRecord(record_id="shared", aggregate_type="t", aggregate_id="a", version=1, payload={})
    )

    with pytest.raises(PersistenceConflictError, match="record_id"):
        repository.create(
            PersistenceRecord(record_id="shared", aggregate_type="t", aggregate_id="b", version=1, payload={})
        )


def test_rewriting_the_same_row_is_not_a_collision():
    """Re-creating the identical row after a retry must stay a version conflict."""
    repository = InMemoryRepository()
    record = PersistenceRecord(
        record_id="r1", aggregate_type="t", aggregate_id="a", version=1, payload={}
    )
    repository.create(record)

    with pytest.raises(PersistenceConflictError, match="already exists"):
        repository.create(record)
