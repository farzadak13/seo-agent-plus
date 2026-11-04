"""A tenant's subscription, and the payments that extend it."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.billing.plans import PlanId


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SubscriptionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime = Field(default_factory=_now)
    # "trial_started", "paid"
    reason: str = Field(min_length=1, max_length=50)
    plan: PlanId
    ends_at: datetime
    payment_id: str | None = None


class Subscription(BaseModel):
    """Active until ``ends_at``; after it, the tenant can read and undo, not start new work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1, max_length=64)
    plan: PlanId
    started_at: datetime = Field(default_factory=_now)
    ends_at: datetime
    history: list[SubscriptionEvent] = Field(default_factory=list)


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class Payment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payment_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    plan: PlanId
    # Tomans, as the price was when the customer pressed pay.
    amount: int = Field(gt=0)
    authority: str | None = Field(default=None, max_length=100)
    status: PaymentStatus = PaymentStatus.PENDING
    ref_id: str | None = Field(default=None, max_length=100)
    # Masked by the gateway (6037-99**-****-1234); never the full number.
    card_pan: str | None = Field(default=None, max_length=32)
    failure: str | None = Field(default=None, max_length=200)
    # The customer came back saying paid, but the verify could not be made:
    # asked again on the next look at the billing page.
    verify_tried: bool = False
    created_at: datetime = Field(default_factory=_now)
    settled_at: datetime | None = None
