"""What a customer can buy, and what each plan lets them do.

Prices are the operator's to set (SEO_AGENT_PLAN_PRICES, tomans a month), so a
change of price is a setting, not a release. A tenant with no subscription at
all is one the operator set up by hand before sign-up existed: it keeps working
as before, without limits, so nothing that already runs breaks.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class PlanId(StrEnum):
    TRIAL = "trial"
    BASIC = "basic"
    GOLD = "gold"


@dataclass(frozen=True)
class Plan:
    plan_id: PlanId
    name: str
    sites: int
    # Tomans for one period; 0 for the trial, which is not sold.
    price: int
    # Written by the cheaper model (SEO_AGENT_ARVAN_BASIC_MODEL), or the best one.
    basic_writer: bool = True
    # Changes approved in any 30 days: a site gets better month by month, not
    # all at once and then nothing left to do.
    changes_a_month: int = 20
    # Words kept per site; each one is a Google search a week.
    targets: int = 10
    # How long after its last finished scan a site is scanned again on its own.
    rescan_every: timedelta = timedelta(days=30)


@dataclass(frozen=True)
class Limits:
    """What a tenant may do; None: no limit (a tenant the operator set up by hand)."""

    changes_a_month: int | None
    targets: int
    rescan_every: timedelta


# A tenant with no subscription keeps working as before sign-up existed.
UNLIMITED = Limits(changes_a_month=None, targets=20, rescan_every=timedelta(days=30))
QUOTA_WINDOW = timedelta(days=30)


TRIAL_LENGTH = timedelta(days=7)
PERIOD = timedelta(days=30)

DEFAULT_PRICES = {PlanId.BASIC: 890_000, PlanId.GOLD: 3_500_000}
_NAMES = {PlanId.TRIAL: "آزمایشی", PlanId.BASIC: "پایه", PlanId.GOLD: "طلایی"}
_SITES = {PlanId.TRIAL: 1, PlanId.BASIC: 1, PlanId.GOLD: 1}
# The trial is the basic plan for a week: what the customer will have if they buy it.
_BASIC_WRITER = {PlanId.TRIAL: True, PlanId.BASIC: True, PlanId.GOLD: False}
_CHANGES = {PlanId.TRIAL: 10, PlanId.BASIC: 20, PlanId.GOLD: 100}
_TARGETS = {PlanId.TRIAL: 10, PlanId.BASIC: 10, PlanId.GOLD: 50}
_RESCAN = {PlanId.TRIAL: timedelta(days=30), PlanId.BASIC: timedelta(days=30), PlanId.GOLD: timedelta(days=7)}
# Zarinpal takes at least 1,000 tomans; anything lower is a typo in the setting.
MIN_PRICE = 1_000


def parse_prices(text: str | None) -> dict[PlanId, int]:
    """"basic=890000,gold=3500000" → prices; plans left out keep their default."""
    prices = dict(DEFAULT_PRICES)
    for part in (text or "").split(","):
        if not part.strip():
            continue
        name, _, value = part.partition("=")
        plan = PlanId(name.strip().lower())
        if plan == PlanId.TRIAL:
            raise ValueError("The trial has no price.")
        price = int(value.strip())
        if price < MIN_PRICE:
            raise ValueError(f"The price of {plan.value} is below {MIN_PRICE} tomans.")
        prices[plan] = price
    return prices


def catalog(prices: dict[PlanId, int] | None = None) -> dict[PlanId, Plan]:
    prices = prices or DEFAULT_PRICES
    return {
        plan: Plan(
            plan_id=plan, name=_NAMES[plan], sites=_SITES[plan],
            price=0 if plan == PlanId.TRIAL else prices[plan], basic_writer=_BASIC_WRITER[plan],
            changes_a_month=_CHANGES[plan], targets=_TARGETS[plan], rescan_every=_RESCAN[plan],
        )
        for plan in PlanId
    }
