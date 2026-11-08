"""What a tenant may do now, starting a trial, and turning a payment into time.

A tenant the operator set up before sign-up existed has no subscription and no
limits. One that signed up starts a trial; when its time runs out it can still
read everything and undo changes, but starts nothing new until it pays.
"""
from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.billing.models import Payment, PaymentStatus, Subscription, SubscriptionEvent
from app.billing.plans import Limits, PERIOD, Plan, PlanId, TRIAL_LENGTH, UNLIMITED
from app.billing.store import PaymentStore, SubscriptionStore
from app.billing.zarinpal import GatewayError, ZarinpalGateway
from app.persistence.contracts import PersistenceConflictError

log = logging.getLogger(__name__)

# A payment nobody came back from: by then the gateway has given up on it, so
# a verify that says "not paid" is the answer, not a payment still under way.
RECHECK_AFTER = timedelta(minutes=20)
CREDIT_ATTEMPTS = 3
# Zarinpal's answer for a payment the customer did not complete.
NOT_PAID_CODES = frozenset({-51})
# Past this, an answer that is still not "paid" is taken as the end of it.
GIVE_UP_AFTER = timedelta(days=2)


class PlanRequired(Exception):
    """The tenant's plan does not allow this now. The message is safe to show."""


class PaymentUnavailable(Exception):
    """No payment could be started. The message is safe to show."""


@dataclass(frozen=True)
class Entitlement:
    # None: set up by the operator, without a subscription or limits.
    plan: PlanId | None
    active: bool
    ends_at: datetime | None
    sites: int | None


class Billing:
    def __init__(
        self,
        *,
        subscriptions: SubscriptionStore,
        payments: PaymentStore,
        plans: dict[PlanId, Plan],
        gateway: ZarinpalGateway | None,
        callback_url: str | None,
        transaction_factory=nullcontext,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        payment_id_factory: Callable[[], str] = lambda: "pay-" + secrets.token_hex(10),
    ) -> None:
        self._subscriptions = subscriptions
        self._payments = payments
        self.plans = plans
        self._gateway = gateway
        self._callback_url = callback_url
        self._transaction = transaction_factory
        self._clock = clock
        self._payment_id = payment_id_factory

    @property
    def can_take_payments(self) -> bool:
        return self._gateway is not None and bool(self._callback_url)

    # --- what the tenant may do ---------------------------------------------------

    def entitlement(self, tenant_id: str) -> Entitlement:
        subscription, _ = self._subscriptions.read(tenant_id)
        if subscription is None:
            return Entitlement(plan=None, active=True, ends_at=None, sites=None)
        return Entitlement(
            plan=subscription.plan,
            active=self._clock() < subscription.ends_at,
            ends_at=subscription.ends_at,
            sites=self.plans[subscription.plan].sites,
        )

    def limits(self, tenant_id: str) -> Limits:
        plan = self.entitlement(tenant_id).plan
        if plan is None:
            return UNLIMITED
        chosen = self.plans[plan]
        return Limits(
            changes_a_month=chosen.changes_a_month, targets=chosen.targets, rescan_every=chosen.rescan_every,
            competitors=chosen.competitors,
        )

    def basic_writer(self, tenant_id: str) -> bool:
        """Whether the cheaper model writes for this tenant (app.reasoning.tiers)."""
        plan = self.entitlement(tenant_id).plan
        return plan is not None and self.plans[plan].basic_writer

    def require_active(self, tenant_id: str) -> Entitlement:
        entitlement = self.entitlement(tenant_id)
        if not entitlement.active:
            raise PlanRequired("Your subscription has ended. Renew it to start new work.")
        return entitlement

    def require_site_room(self, tenant_id: str, sites_now: int) -> None:
        entitlement = self.require_active(tenant_id)
        if entitlement.sites is not None and sites_now >= entitlement.sites:
            raise PlanRequired("Your plan has no room for another site. Upgrade it to add one.")

    def start_trial(self, tenant_id: str) -> Subscription:
        """For a tenant that has just signed up; refused if it ever had a subscription."""
        now = self._clock()
        trial = Subscription(
            tenant_id=tenant_id,
            plan=PlanId.TRIAL,
            started_at=now,
            ends_at=now + TRIAL_LENGTH,
            history=[SubscriptionEvent(at=now, reason="trial_started", plan=PlanId.TRIAL, ends_at=now + TRIAL_LENGTH)],
        )
        return self._subscriptions.save(trial, expected_version=0)

    # --- paying ---------------------------------------------------------------------

    def checkout(self, tenant_id: str, plan_id: PlanId, *, mobile: str | None = None) -> str:
        """The address to send the customer to. Raises PaymentUnavailable when there is none."""
        if not self.can_take_payments:
            raise PaymentUnavailable("Online payment is not available yet.")
        plan = self.plans[plan_id]
        if plan.plan_id == PlanId.TRIAL:
            raise PaymentUnavailable("The trial is not for sale.")
        if self.entitlement(tenant_id).plan is None:
            # Set up by the operator without limits: a purchase would add them.
            raise PaymentUnavailable("This account has no plan to pay for.")
        payment = Payment(payment_id=self._payment_id(), tenant_id=tenant_id, plan=plan.plan_id, amount=plan.price, created_at=self._clock())
        self._payments.save(payment, expected_version=0)
        try:
            authority = self._gateway.request(
                amount_toman=payment.amount,
                description=f"اشتراک یک‌ماههٔ پلن {plan.name} هوشیار سئو",
                callback_url=self._callback_url,
                mobile=mobile,
                order_id=payment.payment_id,
            )
        except GatewayError as exc:
            log.warning("billing: payment request for %s refused (%s)", tenant_id, exc)
            self._payments.save(
                payment.model_copy(update={"status": PaymentStatus.FAILED, "failure": "request_refused", "settled_at": self._clock()}),
                expected_version=1,
            )
            raise PaymentUnavailable("The payment gateway did not answer. Try again in a few minutes.") from exc
        with self._transaction():
            started = self._payments.save(payment.model_copy(update={"authority": authority}), expected_version=1)
            self._payments.link_authority(started)
        return self._gateway.start_url(authority)

    def settle(self, *, authority: str, status: str) -> Payment | None:
        """The customer is back from the gateway. None: no payment of ours has this authority."""
        payment, version = self._payments.by_authority(authority)
        if payment is None:
            return None
        if payment.status != PaymentStatus.PENDING:
            return payment  # a reload of the return page: already settled
        if status != "OK":
            return self._close(payment, version, failure="cancelled")
        return self._verify(payment, version)

    def recheck(self, tenant_id: str) -> None:
        """Pending payments that can be settled now: one whose verify failed on the
        way, and one nobody came back from. Each costs one verify call."""
        if self._gateway is None:
            return
        now = self._clock()
        for payment in self._payments.for_tenant(tenant_id):
            if payment.status != PaymentStatus.PENDING or not payment.authority:
                continue
            if payment.verify_tried or now - payment.created_at >= RECHECK_AFTER:
                current, version = self._payments.read(payment.payment_id)
                if current is not None and current.status == PaymentStatus.PENDING:
                    self._verify(current, version)

    def _verify(self, payment: Payment, version: int) -> Payment:
        try:
            verified = self._gateway.verify(amount_toman=payment.amount, authority=payment.authority)
        except GatewayError as exc:
            # Kept pending and marked: the billing page asks again. Money the
            # gateway took is never left without an answer.
            log.warning("billing: verify of %s could not be done (%s)", payment.payment_id, exc)
            if payment.verify_tried:
                return payment
            try:
                return self._payments.save(payment.model_copy(update={"verify_tried": True}), expected_version=version)
            except PersistenceConflictError:
                return self._payments.read(payment.payment_id)[0]
        if not verified.paid:
            # Only "the payment was not made" settles it. Any other refusal (an
            # unexpected error, too many calls) may hide a payment that went
            # through, so it stays pending until the gateway can say, or until
            # long past the time any payment could still be under way.
            if verified.code in NOT_PAID_CODES or self._clock() - payment.created_at >= GIVE_UP_AFTER:
                return self._close(payment, version, failure=f"not_paid:{verified.code}")
            log.warning("billing: verify of %s answered %s; asked again later", payment.payment_id, verified.code)
            if payment.verify_tried:
                return payment
            try:
                return self._payments.save(payment.model_copy(update={"verify_tried": True}), expected_version=version)
            except PersistenceConflictError:
                return self._payments.read(payment.payment_id)[0]
        return self._credit(payment, version, verified)

    def _close(self, payment: Payment, version: int, *, failure: str) -> Payment:
        failed = payment.model_copy(update={"status": PaymentStatus.FAILED, "failure": failure, "settled_at": self._clock()})
        try:
            return self._payments.save(failed, expected_version=version)
        except PersistenceConflictError:
            return self._payments.read(payment.payment_id)[0]

    def _remaining_in(self, current: Subscription | None, plan: PlanId, now: datetime) -> timedelta:
        """The time left, in the plan being bought: worth what was paid for it.

        A month of gold left becomes about four of basic, and a month of
        basic about a week of gold. The trial's days are a gift, kept as days.
        """
        if current is None or current.ends_at <= now:
            return timedelta(0)
        left = current.ends_at - now
        if current.plan in (plan, PlanId.TRIAL):
            return left
        return left * self.plans[current.plan].price / self.plans[plan].price

    def _credit(self, payment: Payment, version: int, verified) -> Payment:
        """Paid: the payment and its month land together, or neither does.

        Two different payments of one tenant may land at once: the one that
        loses reads the subscription again and adds its month to the new end.
        """
        for _ in range(CREDIT_ATTEMPTS):
            now = self._clock()
            paid = payment.model_copy(
                update={"status": PaymentStatus.PAID, "ref_id": verified.ref_id, "card_pan": verified.card_pan, "settled_at": now}
            )
            try:
                with self._transaction():
                    self._payments.save(paid, expected_version=version)
                    current, sub_version = self._subscriptions.read(payment.tenant_id)
                    ends_at = now + self._remaining_in(current, payment.plan, now) + PERIOD
                    event = SubscriptionEvent(at=now, reason="paid", plan=payment.plan, ends_at=ends_at, payment_id=payment.payment_id)
                    renewed = Subscription(
                        tenant_id=payment.tenant_id,
                        plan=payment.plan,
                        started_at=current.started_at if current is not None else now,
                        ends_at=ends_at,
                        history=[*(current.history if current is not None else []), event][-50:],
                    )
                    self._subscriptions.save(renewed, expected_version=sub_version)
                return paid
            except PersistenceConflictError:
                latest, version = self._payments.read(payment.payment_id)
                if latest is None or latest.status != PaymentStatus.PENDING:
                    return latest  # another return of the same payment got there first
                payment = latest
        log.error("billing: payment %s was paid but could not be credited; it stays pending", payment.payment_id)
        return payment

    def payments(self, tenant_id: str) -> list[Payment]:
        return self._payments.for_tenant(tenant_id)
