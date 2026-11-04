"""Subscriptions and payments, each written over the version it was read at.

Two callbacks for one payment (the customer reloads the bank's return page)
must extend the subscription once: the payment moves from pending to paid
only over the version that was pending, in the same transaction as the
subscription, so the second finds it paid and changes nothing.
"""
from __future__ import annotations

from app.billing.models import Payment, Subscription
from app.models.persistence import PersistenceRecord
from app.persistence.contracts import Repository

SUBSCRIPTION = "subscription"
PAYMENT = "payment"
# The gateway's id for a payment, pointing at ours: a callback carries only it.
PAYMENT_BY_AUTHORITY = "payment_authority"
SCHEMA_VERSION = 1


def _record(kind: str, aggregate_id: str, payload: dict, *, tenant_id: str, version: int) -> PersistenceRecord:
    return PersistenceRecord(
        record_id=f"{kind}:{aggregate_id}:v{version}",
        aggregate_type=kind,
        aggregate_id=aggregate_id,
        schema_version=SCHEMA_VERSION,
        version=version,
        payload=payload,
        tenant_id=tenant_id,
    )


class _Versioned:
    def __init__(self, repository: Repository) -> None:
        self._repository = repository

    def _write(self, kind: str, aggregate_id: str, payload: dict, *, tenant_id: str, expected_version: int) -> None:
        record = _record(kind, aggregate_id, payload, tenant_id=tenant_id, version=expected_version + 1)
        if expected_version:
            self._repository.replace(record, expected_version=expected_version)
        else:
            self._repository.create(record)


class SubscriptionStore(_Versioned):
    def read(self, tenant_id: str) -> tuple[Subscription | None, int]:
        record = self._repository.get(aggregate_type=SUBSCRIPTION, aggregate_id=tenant_id)
        return (Subscription.model_validate(record.payload), record.version) if record else (None, 0)

    def save(self, subscription: Subscription, *, expected_version: int) -> Subscription:
        self._write(
            SUBSCRIPTION, subscription.tenant_id, subscription.model_dump(mode="json"),
            tenant_id=subscription.tenant_id, expected_version=expected_version,
        )
        return subscription


class PaymentStore(_Versioned):
    def read(self, payment_id: str) -> tuple[Payment | None, int]:
        record = self._repository.get(aggregate_type=PAYMENT, aggregate_id=payment_id)
        return (Payment.model_validate(record.payload), record.version) if record else (None, 0)

    def save(self, payment: Payment, *, expected_version: int) -> Payment:
        self._write(
            PAYMENT, payment.payment_id, payment.model_dump(mode="json"),
            tenant_id=payment.tenant_id, expected_version=expected_version,
        )
        return payment

    def link_authority(self, payment: Payment) -> None:
        """Written once, when the gateway hands out the authority."""
        self._repository.create(
            _record(
                PAYMENT_BY_AUTHORITY, str(payment.authority), {"payment_id": payment.payment_id},
                tenant_id=payment.tenant_id, version=1,
            )
        )

    def by_authority(self, authority: str) -> tuple[Payment | None, int]:
        index = self._repository.get(aggregate_type=PAYMENT_BY_AUTHORITY, aggregate_id=authority)
        if index is None:
            return None, 0
        return self.read(str(index.payload["payment_id"]))

    def for_tenant(self, tenant_id: str, *, limit: int = 20) -> list[Payment]:
        page = self._repository.query(tenant_id=tenant_id, aggregate_type=PAYMENT, limit=limit)
        return [Payment.model_validate(record.payload) for record in page.records]
