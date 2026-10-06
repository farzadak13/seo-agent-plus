"""Accounts and sessions against a real PostgreSQL.

The uniqueness of an email and of a Google identity rests on the record
store's primary key, so it is checked where that key really exists.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live account tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
                allow_module_level=True)

from app.accounts.service import AuthService, SignInError  # noqa: E402
from app.accounts.store import AccountExistsError, AccountStore, SessionStore  # noqa: E402
from app.models.tenants import Tenant, TenantStatus  # noqa: E402
from app.persistence.migrator import apply_migrations  # noqa: E402
from app.persistence.postgres import PostgresRepository  # noqa: E402
from app.tenancy.store import TenantStore  # noqa: E402

PASSWORD = "a long enough passphrase"


@pytest.fixture(autouse=True)
def clean_database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations CASCADE")
    apply_migrations(dsn=DSN, directory="migrations")
    tenants = TenantStore(PostgresRepository(DSN))
    for tenant_id in ("t1", "t2"):
        tenants.create(Tenant(tenant_id=tenant_id, name=tenant_id))


def auth(when=datetime(2026, 10, 5, 12, tzinfo=timezone.utc)):
    repository = PostgresRepository(DSN)
    return AuthService(accounts=AccountStore(repository), sessions=SessionStore(repository),
                       tenants=TenantStore(repository), clock=lambda: when)


def test_sign_in_round_trip():
    service = auth()
    service.create_account(tenant_id="t1", email="owner@tennisino.com", password=PASSWORD)
    token, session = service.sign_in_with_password(email="owner@tennisino.com", password=PASSWORD, address="1")
    found = auth().session_for(token)  # a fresh service: nothing carried over but the database
    assert found is not None and found[0].tenant_id == "t1"
    auth().sign_out(token)
    assert auth().session_for(token) is None


def test_two_accounts_with_one_email_cannot_both_exist_even_at_once():
    errors, created = [], []

    def create(tenant):
        try:
            created.append(auth().create_account(tenant_id=tenant, email="same@x.com", password=None))
        except AccountExistsError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(f"t{n}",)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(created) == 1 and len(errors) == 3


def test_a_google_identity_links_to_one_account_only():
    service = auth()
    service.create_account(tenant_id="t1", email="a@x.com", password=None)
    service.create_account(tenant_id="t2", email="b@x.com", password=None)
    service.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)

    store = AccountStore(PostgresRepository(DSN))
    with pytest.raises(AccountExistsError):
        store.bind_google(store.by_email("b@x.com"), "g-1")
    assert store.by_google_subject("g-1").email == "a@x.com"


def test_an_unlinked_google_identity_can_move_to_another_tenants_account():
    # The store refuses to move a record between tenants; the index has none.
    service = auth()
    service.create_account(tenant_id="t1", email="a@x.com", password=None)
    service.create_account(tenant_id="t2", email="b@x.com", password=None)
    service.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    store = AccountStore(PostgresRepository(DSN))
    service.unlink_google(store.by_email("a@x.com"))

    service.sign_in_with_google(subject="g-1", email="b@x.com", email_verified=True)

    assert store.by_google_subject("g-1").email == "b@x.com"
    assert store.by_email("a@x.com").google_subject is None


# ---- the operator's commands ------------------------------------------------------------


@pytest.fixture
def cli(monkeypatch, capsys):
    import getpass

    from app.accounts.__main__ import main

    monkeypatch.setenv("SEO_AGENT_DATABASE_DSN", DSN)
    typed = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": typed.pop(0))

    def run(*argv, passwords=()):
        typed[:] = list(passwords)
        code = main(list(argv))
        out = capsys.readouterr()
        return code, out.out + out.err

    return run


def test_the_cli_creates_an_account_and_refuses_an_unknown_tenant(cli):
    code, out = cli("create", "--tenant", "t9", "--email", "a@x.com", passwords=(PASSWORD, PASSWORD))
    assert code == 1 and "No tenant t9" in out
    code, out = cli("create", "--tenant", "t1", "--email", "a@x.com", passwords=(PASSWORD, PASSWORD))
    assert code == 0 and "created a@x.com" in out
    assert PASSWORD not in out
    code, out = cli("list", "--tenant", "t1")
    assert "a@x.com  active  password  google not linked" in out


def test_the_cli_refuses_two_different_passwords(cli):
    cli("create", "--tenant", "t1", "--email", "a@x.com", passwords=(PASSWORD, PASSWORD))
    with pytest.raises(SystemExit, match="differ"):
        cli("set-password", "--email", "a@x.com", passwords=("another long passphrase", "something else here"))
    auth().sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")


def test_each_cli_command_ends_the_sessions_it_should(cli):
    cli("create", "--tenant", "t1", "--email", "a@x.com", passwords=(PASSWORD, PASSWORD))

    def signed_in(password=PASSWORD):
        return auth().sign_in_with_password(email="a@x.com", password=password, address="1")[0]

    token = signed_in()
    assert cli("revoke-sessions", "--email", "a@x.com")[0] == 0
    assert auth().session_for(token) is None

    token = signed_in()
    new = "another long passphrase"
    assert cli("set-password", "--email", "a@x.com", passwords=(new, new))[0] == 0
    assert auth().session_for(token) is None
    with pytest.raises(SignInError):
        signed_in()
    token = signed_in(new)

    assert cli("disable", "--email", "a@x.com")[0] == 0
    assert auth().session_for(token) is None
    with pytest.raises(SignInError):
        signed_in(new)
    assert cli("enable", "--email", "a@x.com")[0] == 0
    assert auth().session_for(token) is None, "enabling does not bring old sessions back"
    assert auth().session_for(signed_in(new)) is not None


def test_the_cli_unlinks_google(cli):
    cli("create", "--tenant", "t1", "--email", "a@x.com", "--google-only")
    token, _ = auth().sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    code, out = cli("unlink-google", "--email", "a@x.com")
    assert code == 0 and "Google sign-in is now off" in out
    assert auth().session_for(token) is None
    assert AccountStore(PostgresRepository(DSN)).by_google_subject("g-1") is None
    with pytest.raises(SignInError):
        auth().sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    assert "google blocked" in cli("list", "--tenant", "t1")[1]

    code, out = cli("allow-google", "--email", "a@x.com")
    assert code == 0 and "may now link itself" in out
    again, _ = auth().sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    assert auth().session_for(again) is not None


# ---- linking is one write, and a suspended tenant is shut out ----------------------------


def connection_drops(self, account):
    raise RuntimeError("the connection dropped")


def test_a_link_that_fails_halfway_leaves_nothing_behind(monkeypatch):
    service = auth()
    service.create_account(tenant_id="t1", email="a@x.com", password=None)
    store = AccountStore(PostgresRepository(DSN))

    monkeypatch.setattr(AccountStore, "update", connection_drops)
    with pytest.raises(RuntimeError):
        store.bind_google(store.by_email("a@x.com"), "g-1")
    monkeypatch.undo()

    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM persistence_records WHERE aggregate_type = 'account_google'")
        assert cursor.fetchone()[0] == 0, "the index record was rolled back with the account write"
    token, _ = auth().sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    assert auth().session_for(token)[1].google_subject == "g-1", "and Google sign-in still works afterwards"


def test_unlinking_is_one_write_too(monkeypatch):
    service = auth()
    service.create_account(tenant_id="t1", email="a@x.com", password=None)
    service.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    store = AccountStore(PostgresRepository(DSN))
    monkeypatch.setattr(AccountStore, "update", connection_drops)
    with pytest.raises(RuntimeError):
        service.unlink_google(store.by_email("a@x.com"))
    monkeypatch.undo()
    assert store.by_google_subject("g-1").email == "a@x.com", "nothing half-unlinked"


def test_a_suspended_tenant_is_shut_out_of_the_dashboard():
    service = auth()
    service.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = service.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    tenants = TenantStore(PostgresRepository(DSN))
    tenants.update(tenants.get("t1").model_copy(update={"status": TenantStatus.SUSPENDED}))

    assert auth().session_for(token) is None
    with pytest.raises(SignInError, match="suspended"):
        auth().sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")


def test_the_cli_names_an_unknown_email(cli):
    code, out = cli("disable", "--email", "nobody@x.com")
    assert code == 1 and "No account for nobody@x.com" in out


# ---- clearing out records nobody can use ------------------------------------------------


def test_the_purge_removes_only_what_can_no_longer_be_used():
    from datetime import timedelta

    from app.accounts.google_login import STATE_AGGREGATE as LOGIN_STATE
    from app.accounts.store import SESSION
    from app.models.persistence import PersistenceRecord
    from app.runtime.maintenance import RecordPurge

    now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
    repository = PostgresRepository(DSN)
    sessions = SessionStore(repository)

    def at(when):
        return auth(when)

    at(now).create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)

    def open_session(when):
        return at(when).sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")[0]

    live = open_session(now - timedelta(hours=2))
    at(now - timedelta(minutes=30)).session_for(live)  # touched: a second version
    expired = open_session(now - timedelta(days=16))
    revoked = open_session(now - timedelta(days=3))
    at(now - timedelta(days=2)).sign_out(revoked)
    recently_revoked = open_session(now - timedelta(hours=3))
    at(now - timedelta(hours=1)).sign_out(recently_revoked)
    for state_id, age in (("old", timedelta(days=2)), ("new", timedelta(minutes=5))):
        repository.create(PersistenceRecord(
            record_id=f"login-state:{state_id}:v1", aggregate_type=LOGIN_STATE, aggregate_id=state_id,
            version=1, payload={"state_id": state_id}, created_at=now - age,
        ))

    removed = RecordPurge(DSN, clock=lambda: now).run_round()

    assert removed["states"] == 1
    assert repository.get(aggregate_type=LOGIN_STATE, aggregate_id="old") is None
    assert repository.get(aggregate_type=LOGIN_STATE, aggregate_id="new") is not None
    from app.accounts.service import token_hash

    assert sessions.get(token_hash(expired)) is None and sessions.get(token_hash(revoked)) is None
    assert sessions.get(token_hash(recently_revoked)) is not None, "kept for a day after revoking"
    with psycopg.connect(DSN) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT version FROM persistence_records WHERE aggregate_type = %s AND aggregate_id = %s",
                       (SESSION, token_hash(live)))
        assert [row[0] for row in cursor.fetchall()] == [2], "only the latest version is kept"
    assert at(now).session_for(live) is not None, "a live session keeps working, and can still be touched"
    assert sessions.get(token_hash(live))[1] == 3
    assert RecordPurge(DSN, clock=lambda: now).run_round() == {
        "states": 0, "dead_sessions": 0, "superseded_session_versions": 1  # version 2, now that 3 exists
    }


def test_turning_google_off_just_before_a_first_link_wins(monkeypatch):
    service = auth()
    account = service.create_account(tenant_id="t1", email="a@x.com", password=None)
    store = service._accounts
    real_bind = store.bind_google

    def operator_then_bind(stale, subject):
        auth().unlink_google(AccountStore(PostgresRepository(DSN)).get(account.account_id))
        return real_bind(stale, subject)

    monkeypatch.setattr(store, "bind_google", operator_then_bind)
    with pytest.raises(SignInError):
        service.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)

    fresh = AccountStore(PostgresRepository(DSN))
    assert fresh.by_google_subject("g-1") is None
    assert fresh.get(account.account_id).google_subject is None and not fresh.get(account.account_id).google_link_allowed
