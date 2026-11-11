"""Sign-up and payment against a real PostgreSQL: what must land together, or not at all.

In memory a transaction cannot be shown to roll back; here it is the database's.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

DSN = os.getenv("SEO_AGENT_TEST_DSN")
psycopg = pytest.importorskip("psycopg", reason="psycopg is required for live database tests")

if not DSN:
    pytest.skip("SEO_AGENT_TEST_DSN is not set; live billing tests are skipped.", allow_module_level=True)

try:
    with psycopg.connect(DSN, connect_timeout=5) as _probe:
        with _probe.cursor() as _cursor:
            _cursor.execute("SELECT 1")
except Exception as _exc:  # noqa: BLE001
    pytest.skip(f"SEO_AGENT_TEST_DSN is set but the database cannot be reached. Error: {_exc}",
                allow_module_level=True)

from app.accounts.service import AuthService  # noqa: E402
from app.accounts.store import AccountExistsError, AccountStore, SessionStore  # noqa: E402
from app.billing.models import Payment, PaymentStatus  # noqa: E402
from app.billing.plans import PERIOD, PlanId, catalog  # noqa: E402
from app.billing.service import Billing  # noqa: E402
from app.billing.store import PaymentStore, SubscriptionStore  # noqa: E402
from app.persistence.migrator import apply_migrations  # noqa: E402
from app.persistence.postgres import PostgresRepository  # noqa: E402
from app.tenancy.store import TenantStore  # noqa: E402

NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc)
MOBILE = "09121234567"


@pytest.fixture(autouse=True)
def clean_database():
    with psycopg.connect(DSN, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS persistence_records, schema_migrations CASCADE")
    apply_migrations(dsn=DSN, directory="migrations")


def records_of(kind: str) -> int:
    with psycopg.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(DISTINCT aggregate_id) FROM persistence_records WHERE aggregate_type = %s", (kind,))
            return cursor.fetchone()[0]


def tenant_count() -> int:
    return records_of("tenant")


def services(repository):
    auth = AuthService(accounts=AccountStore(repository), sessions=SessionStore(repository),
                       tenants=TenantStore(repository), clock=lambda: NOW)
    billing = Billing(subscriptions=SubscriptionStore(repository), payments=PaymentStore(repository), plans=catalog(),
                      gateway=None, callback_url=None, transaction_factory=repository.transaction, clock=lambda: NOW)
    return auth, billing


def test_a_sign_up_refused_by_the_account_leaves_no_tenant_behind():
    repository = PostgresRepository(DSN)
    auth, _ = services(repository)
    with repository.transaction():
        auth.sign_up_with_mobile(mobile=MOBILE, business_name="فروشگاه اول")
    assert tenant_count() == 1

    # The same number again: the account's own index refuses it, inside the transaction.
    with pytest.raises(AccountExistsError):
        with repository.transaction():
            auth.sign_up_with_mobile(mobile=MOBILE, business_name="فروشگاه دوم")
    assert tenant_count() == 1, "the second tenant went back with its account"


def test_tenant_account_and_trial_land_together_or_not_at_all():
    repository = PostgresRepository(DSN)
    auth, billing = services(repository)

    class TrialRefused(Exception):
        pass

    with pytest.raises(TrialRefused):
        with repository.transaction():
            _account, tenant = auth.sign_up_with_mobile(mobile=MOBILE, business_name="فروشگاه")
            billing.start_trial(tenant.tenant_id)
            raise TrialRefused  # anything after the trial, before the end
    assert tenant_count() == 0
    assert AccountStore(repository).by_mobile(MOBILE) is None
    assert records_of("subscription") == 0, "the trial went back too"

    with repository.transaction():
        _account, tenant = auth.sign_up_with_mobile(mobile=MOBILE, business_name="فروشگاه")
        billing.start_trial(tenant.tenant_id)
    assert billing.entitlement(tenant.tenant_id).plan == PlanId.TRIAL


def test_a_payment_credited_while_another_lands_reads_again_and_adds_its_month():
    repository = PostgresRepository(DSN)
    _, billing = services(repository)
    billing.start_trial("t-1")
    payments = PaymentStore(repository)
    payment = payments.save(Payment(payment_id="pay-1", tenant_id="t-1", plan=PlanId.BASIC, amount=890_000), expected_version=0)

    # Another payment's month lands, on its own connection, just before this one writes.
    elsewhere = SubscriptionStore(PostgresRepository(DSN))
    subscriptions = billing._subscriptions
    original_save = subscriptions.save
    landed = []

    def save_after_another(subscription, *, expected_version):
        if not landed:
            current, version = elsewhere.read("t-1")
            other = current.model_copy(update={"plan": PlanId.BASIC, "ends_at": current.ends_at + PERIOD})
            elsewhere.save(other, expected_version=version)
            landed.append(other.ends_at)
        return original_save(subscription, expected_version=expected_version)

    subscriptions.save = save_after_another
    paid = billing._credit(payment, 1, SimpleNamespace(ref_id="R1", card_pan="6037-99**-****-1234"))

    assert paid.status == PaymentStatus.PAID and paid.ref_id == "R1"
    stored, version = payments.read("pay-1")
    assert stored.status == PaymentStatus.PAID and version == 2, "the payment was written once, by the try that landed"
    subscription, _ = SubscriptionStore(repository).read("t-1")
    assert subscription.ends_at == landed[0] + PERIOD, "the other payment's month is kept, and this one added to it"
    assert [event.reason for event in subscription.history].count("paid") == 1
    assert subscription.ends_at - NOW > timedelta(days=60)
