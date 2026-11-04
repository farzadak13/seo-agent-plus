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
    STARTER = "starter"
    GROWTH = "growth"
    PRO = "pro"


@dataclass(frozen=True)
class Plan:
    plan_id: PlanId
    name: str
    sites: int
    # Tomans for one period; 0 for the trial, which is not sold.
    price: int


TRIAL_LENGTH = timedelta(days=7)
PERIOD = timedelta(days=30)

DEFAULT_PRICES = {PlanId.STARTER: 490_000, PlanId.GROWTH: 1_290_000, PlanId.PRO: 2_990_000}
_NAMES = {PlanId.TRIAL: "آزمایشی", PlanId.STARTER: "پایه", PlanId.GROWTH: "رشد", PlanId.PRO: "حرفه‌ای"}
_SITES = {PlanId.TRIAL: 1, PlanId.STARTER: 1, PlanId.GROWTH: 3, PlanId.PRO: 10}
# Zarinpal takes at least 1,000 tomans; anything lower is a typo in the setting.
MIN_PRICE = 1_000


def parse_prices(text: str | None) -> dict[PlanId, int]:
    """"starter=490000,growth=1290000" → prices; plans left out keep their default."""
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
        plan: Plan(plan_id=plan, name=_NAMES[plan], sites=_SITES[plan], price=0 if plan == PlanId.TRIAL else prices[plan])
        for plan in PlanId
    }
