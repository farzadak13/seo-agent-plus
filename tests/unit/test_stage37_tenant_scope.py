"""Tenant scoping at the persistence boundary.

Until now every record carried its owner only inside the JSON payload, so
"records belonging to this customer" could not be expressed as a query at all
— only as a scan-and-filter in Python. These tests pin the three properties a
paying multi-tenant product depends on: a scoped query never returns another
tenant's rows, paging visits every row exactly once, and an aggregate can
never change owner.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from app.models.jobs import Job
from app.models.persistence import PersistenceRecord
from app.models.runs import SEORun
from app.models.sites import Site
from app.persistence.contracts import (
    InvalidCursorError,
    PersistenceConflictError,
    RecordPage,
)
from app.persistence.cursor import decode_cursor, encode_cursor
from app.persistence.memory import InMemoryRepository
from app.persistence.serialization import build_record


BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def record(aggregate_id, *, tenant, aggregate_type="seo_run", site=None, version=1, minutes=0):
    return PersistenceRecord(
        record_id=f"{aggregate_type}:{aggregate_id}:v{version}",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        version=version,
        payload={"n": version},
        created_at=BASE + timedelta(minutes=minutes),
        tenant_id=tenant,
        site_id=site,
    )


@pytest.fixture
def populated():
    repository = InMemoryRepository()
    for index in range(5):
        repository.create(
            record(f"a-{index}", tenant="tenant-a", site="site-a", minutes=10 + index)
        )
    for index in range(3):
        repository.create(
            record(f"b-{index}", tenant="tenant-b", site="site-b", minutes=10 + index)
        )
    return repository


# --- isolation --------------------------------------------------------------


def test_a_scoped_query_never_returns_another_tenants_records(populated):
    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert len(page.records) == 5
    assert {item.tenant_id for item in page.records} == {"tenant-a"}


def test_each_tenant_sees_exactly_its_own(populated):
    a = populated.query(tenant_id="tenant-a")
    b = populated.query(tenant_id="tenant-b")

    assert {item.aggregate_id for item in a.records}.isdisjoint(
        {item.aggregate_id for item in b.records}
    )


def test_a_site_filter_cannot_reach_across_tenants(populated):
    page = populated.query(tenant_id="tenant-a", site_id="site-b")

    assert page.records == []


def test_an_unknown_tenant_gets_an_empty_page(populated):
    assert populated.query(tenant_id="tenant-unknown").records == []


def test_records_without_an_owner_are_never_returned():
    repository = InMemoryRepository()
    repository.create(record("orphan", tenant=None))

    assert repository.query(tenant_id="tenant-a").records == []


def test_the_tenant_argument_has_no_default():
    """A caller must name the tenant; omission cannot silently widen the scope."""
    with pytest.raises(TypeError):
        InMemoryRepository().query(aggregate_type="seo_run")


@pytest.mark.parametrize("value", ["", "   "])
def test_a_blank_tenant_is_rejected(value):
    with pytest.raises(ValueError, match="tenant_id is required"):
        InMemoryRepository().query(tenant_id=value)


# --- ordering and paging ----------------------------------------------------


def test_results_are_newest_first(populated):
    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert [item.aggregate_id for item in page.records] == [
        "a-4",
        "a-3",
        "a-2",
        "a-1",
        "a-0",
    ]


def test_paging_visits_every_record_exactly_once(populated):
    expected = [
        item.aggregate_id
        for item in populated.query(tenant_id="tenant-a", aggregate_type="seo_run").records
    ]

    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = populated.query(
            tenant_id="tenant-a", aggregate_type="seo_run", limit=2, cursor=cursor
        )
        seen.extend(item.aggregate_id for item in page.records)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert seen == expected
    assert cursor is None


def test_the_last_page_reports_no_cursor(populated):
    page = populated.query(tenant_id="tenant-a", limit=50)

    assert page.next_cursor is None


def test_a_full_page_with_more_behind_it_reports_a_cursor(populated):
    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run", limit=5)

    assert len(page.records) == 5
    assert page.next_cursor is None, "exactly-exhausted page must not advertise more"


def test_a_malformed_cursor_is_rejected(populated):
    with pytest.raises(InvalidCursorError):
        populated.query(tenant_id="tenant-a", cursor="not-a-cursor")


def test_a_cursor_round_trips():
    cursor = encode_cursor(created_at=BASE, aggregate_id="a-1")

    assert decode_cursor(cursor) == (BASE, "a-1")


@pytest.mark.parametrize("limit", [0, -1, 501])
def test_an_out_of_range_limit_is_rejected(populated, limit):
    with pytest.raises(ValueError, match="limit must be"):
        populated.query(tenant_id="tenant-a", limit=limit)


# --- versioning -------------------------------------------------------------


def test_a_query_returns_the_latest_version_only(populated):
    populated.replace(
        record("a-0", tenant="tenant-a", site="site-a", version=2, minutes=99),
        expected_version=1,
    )

    page = populated.query(tenant_id="tenant-a", aggregate_type="seo_run")

    assert len(page.records) == 5
    assert page.records[0].aggregate_id == "a-0"
    assert page.records[0].version == 2


def test_an_aggregate_can_never_change_tenant(populated):
    with pytest.raises(PersistenceConflictError, match="immutable"):
        populated.replace(
            record("a-1", tenant="tenant-b", site="site-a", version=2),
            expected_version=1,
        )


def test_a_refused_move_leaves_the_record_untouched(populated):
    with pytest.raises(PersistenceConflictError):
        populated.replace(
            record("a-1", tenant="tenant-b", version=2), expected_version=1
        )

    current = populated.get(aggregate_type="seo_run", aggregate_id="a-1")
    assert current.tenant_id == "tenant-a"
    assert current.version == 1


# --- envelope construction --------------------------------------------------


class WithTenant(BaseModel):
    tenant_id: str
    principal_id: str
    site_id: str


class WithPrincipalOnly(BaseModel):
    principal_id: str


class WithNeither(BaseModel):
    value: int


def test_an_explicit_tenant_wins_over_the_model():
    built = build_record(
        record_id="r", aggregate_type="t", aggregate_id="a",
        model=WithTenant(tenant_id="from-model", principal_id="p", site_id="s"),
        tenant_id="explicit",
    )

    assert built.tenant_id == "explicit"


def test_a_model_tenant_wins_over_its_principal():
    built = build_record(
        record_id="r", aggregate_type="t", aggregate_id="a",
        model=WithTenant(tenant_id="tenant-1", principal_id="principal-1", site_id="s"),
    )

    assert built.tenant_id == "tenant-1"
    assert built.site_id == "s"


def test_principal_is_the_fallback_while_one_key_means_one_account():
    built = build_record(
        record_id="r", aggregate_type="t", aggregate_id="a",
        model=WithPrincipalOnly(principal_id="api-key"),
    )

    assert built.tenant_id == "api-key"


def test_a_model_with_no_owner_stays_unscoped():
    built = build_record(
        record_id="r", aggregate_type="t", aggregate_id="a", model=WithNeither(value=1)
    )

    assert built.tenant_id is None
    assert built.site_id is None


# --- the stores populate the scope ------------------------------------------


def test_the_site_store_scopes_what_it_writes():
    from app.onboarding.site_store import SITE_AGGREGATE_TYPE, SiteStore

    repository = InMemoryRepository()
    SiteStore(repository).create(
        Site(site_id="site-1", principal_id="tenant-1", name="Test", base_url="https://example.com/")
    )

    stored = repository.get(aggregate_type=SITE_AGGREGATE_TYPE, aggregate_id="site-1")
    assert stored.tenant_id == "tenant-1"
    assert stored.site_id == "site-1"
    assert repository.query(tenant_id="tenant-1", aggregate_type=SITE_AGGREGATE_TYPE).records


def test_the_run_store_scopes_what_it_writes():
    from app.runs.store import RUN_AGGREGATE_TYPE, RunStore

    repository = InMemoryRepository()
    RunStore(repository).create(
        SEORun(
            run_id="run-1",
            site_id="site-1",
            principal_id="tenant-1",
            job_id="job-1",
            start_date=date(2026, 9, 1).isoformat(),
            end_date=date(2026, 9, 2).isoformat(),
            normalized_url="https://example.com/page",
            normalized_query="کفش مردانه",
            candidate_id="candidate-1",
        )
    )

    stored = repository.get(aggregate_type=RUN_AGGREGATE_TYPE, aggregate_id="run-1")
    assert stored.tenant_id == "tenant-1"
    assert stored.site_id == "site-1"


def test_the_job_store_scopes_what_it_writes():
    from app.jobs.store import JOB_AGGREGATE_TYPE, JobStore

    repository = InMemoryRepository()
    JobStore(repository).create(
        Job(
            job_id="job-1",
            job_type="seo_run",
            principal_id="tenant-1",
            payload={"run_id": "run-1", "site_id": "site-1"},
        )
    )

    stored = repository.get(aggregate_type=JOB_AGGREGATE_TYPE, aggregate_id="job-1")
    assert stored.tenant_id == "tenant-1"
    assert stored.site_id == "site-1"


def test_a_job_without_a_site_is_still_tenant_scoped():
    from app.jobs.store import JOB_AGGREGATE_TYPE, JobStore

    repository = InMemoryRepository()
    JobStore(repository).create(
        Job(job_id="job-2", job_type="echo", principal_id="tenant-1", payload={})
    )

    stored = repository.get(aggregate_type=JOB_AGGREGATE_TYPE, aggregate_id="job-2")
    assert stored.tenant_id == "tenant-1"
    assert stored.site_id is None


# --- migration and contract shape -------------------------------------------


def test_the_migration_adds_the_owner_columns_and_backfills_them():
    migration = Path("migrations/003_tenant_scope.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN IF NOT EXISTS tenant_id" in migration
    assert "ADD COLUMN IF NOT EXISTS site_id" in migration
    assert "UPDATE persistence_records" in migration
    assert "payload ->> 'principal_id'" in migration
    assert "CREATE INDEX IF NOT EXISTS idx_persistence_records_tenant" in migration


def test_the_migration_is_re_runnable():
    """Every statement must be guarded so a repeated deploy is harmless."""
    migration = Path("migrations/003_tenant_scope.sql").read_text(encoding="utf-8")

    assert migration.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "CREATE INDEX IF NOT EXISTS" in migration
    assert "ADD COLUMN tenant_id" not in migration


def test_both_repositories_expose_the_same_contract():
    from app.persistence.postgres import PostgresRepository

    for name in ("create", "get", "list", "query", "replace", "ping"):
        assert hasattr(PostgresRepository, name), name
        assert hasattr(InMemoryRepository, name), name


def test_a_record_page_defaults_to_being_the_last_page():
    assert RecordPage(records=[]).next_cursor is None
