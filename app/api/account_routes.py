"""Signing up and in with a mobile number, and paying for a plan."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from app.billing.quota import Quota, quota
from app.accounts.otp import OtpError, OtpTooSoon
from app.accounts.phone import normalize_mobile
from app.accounts.service import SignInError
from app.accounts.store import AccountExistsError
from app.billing.plans import Limits, PlanId, UNLIMITED
from app.billing.service import PaymentUnavailable, PlanRequired
from app.billing.zarinpal import CALLBACK_PATH


class MobileCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(min_length=10, max_length=20)


class MobileVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(min_length=10, max_length=20)
    code: str = Field(min_length=4, max_length=12)
    # Only for a number with no account yet: who is signing up, and their yes to the terms.
    business_name: str | None = Field(default=None, max_length=200)
    accept_terms: bool = False


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PlanId


def require_plan(dependencies, tenant_id: str) -> None:
    """New work needs a live subscription; reading and undoing never do."""
    if dependencies.billing is None:
        return
    try:
        dependencies.billing.require_active(tenant_id)
    except PlanRequired as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc


def limits_of(dependencies, tenant_id: str) -> Limits:
    """The tenant's plan limits; without billing, as before plans existed."""
    if dependencies.billing is None:
        return UNLIMITED
    return dependencies.billing.limits(tenant_id)


def _changes(dependencies, tenant_id: str, now) -> Quota:
    return quota(dependencies.action_store, tenant_id, limits_of(dependencies, tenant_id).changes_a_month, now)


def _changes_answer(found: Quota) -> dict:
    return {"used": found.used, "limit": found.limit, "frees_at": found.frees_at.isoformat() if found.frees_at else None}


def require_change_room(dependencies, tenant_id: str) -> None:
    """An approval is refused once the plan's changes for any 30 days are used."""
    found = _changes(dependencies, tenant_id, dependencies.clock())
    if found.left == 0:
        frees = found.frees_at.date().isoformat() if found.frees_at else ""
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"This plan's changes for this month are used up; the next one frees up on {frees}.",
        )


def require_site_room(dependencies, tenant_id: str, sites_now: int) -> None:
    if dependencies.billing is None:
        return
    try:
        dependencies.billing.require_site_room(tenant_id, sites_now)
    except PlanRequired as exc:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(exc)) from exc


def register_account_routes(
    app: FastAPI,
    dependencies,
    *,
    principal_id: Callable,
    signed_in: Callable,
    set_session_cookie: Callable,
    require_own_origin: Callable,
    session_cookie: str,
) -> None:
    def otp():
        if dependencies.otp is None or dependencies.auth is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in by mobile is not configured.")
        return dependencies.otp

    def mobile_of(text: str) -> str:
        mobile = normalize_mobile(text)
        if mobile is None:
            raise HTTPException(status_code=422, detail="That is not an Iranian mobile number.")
        return mobile

    def address_of(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @app.get("/v1/auth/options")
    def sign_in_options() -> dict:
        """Which ways in this server offers, so the sign-in page shows only those that work."""
        mobile = dependencies.otp is not None and dependencies.auth is not None
        return {
            "mobile": mobile,
            "signup": mobile and dependencies.signup_enabled,
            "google": dependencies.google_login is not None,
        }

    @app.post("/v1/auth/mobile/code", status_code=status.HTTP_202_ACCEPTED)
    def send_code(request: MobileCodeRequest, http_request: Request) -> dict:
        service = otp()
        require_own_origin(dependencies, http_request)
        mobile = mobile_of(request.mobile)
        if not dependencies.signup_enabled and dependencies.auth.account_for_mobile(mobile) is None:
            # Nothing to sign in to and no sign-up: no SMS paid for. The answer is the same.
            return {"sent": True, "wait_seconds": 60}
        try:
            service.send(mobile=mobile, address=address_of(http_request))
        except OtpTooSoon as exc:
            raise HTTPException(status_code=429, detail={"message": str(exc), "wait_seconds": exc.wait_seconds}) from exc
        except OtpError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        # The same answer whether the number has an account or not.
        return {"sent": True, "wait_seconds": 60}

    @app.post("/v1/auth/mobile/verify")
    def verify_code(request: MobileVerifyRequest, http_request: Request):
        service = otp()
        auth = dependencies.auth
        require_own_origin(dependencies, http_request)
        mobile = mobile_of(request.mobile)
        address = address_of(http_request)
        existing = auth.account_for_mobile(mobile)
        signing_up = existing is None
        details = bool(request.business_name and request.business_name.strip()) and request.accept_terms
        # A new number without its details: the code is checked but kept, so
        # the same code goes back with the name. Only its holder learns the
        # number is new.
        consume = not signing_up or (details and dependencies.signup_enabled)
        try:
            right = service.check(mobile=mobile, code=request.code, address=address, consume=consume)
        except OtpError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        if not right:
            raise HTTPException(status_code=401, detail="The code is not correct or has expired.")
        if signing_up:
            if not dependencies.signup_enabled:
                raise HTTPException(status_code=403, detail="Sign-up is not open yet.")
            if not details:
                return JSONResponse({"status": "needs_signup"})
            # Tenant, account and trial together, or none of them: an account
            # left without its trial would count as one the operator set up,
            # with no limits at all.
            try:
                with dependencies.transaction_factory():
                    _account, tenant = auth.sign_up_with_mobile(mobile=mobile, business_name=request.business_name)
                    if dependencies.billing is not None:
                        dependencies.billing.start_trial(tenant.tenant_id)
            except AccountExistsError:
                pass  # signed up a moment ago by a request racing this one: sign in below
        try:
            token, session = auth.sign_in_with_mobile(mobile=mobile)
        except SignInError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        response = JSONResponse(signed_in(dependencies, session).model_dump(mode="json"))
        set_session_cookie(response, token)
        return response

    # --- the plan and paying for it --------------------------------------------------

    def billing():
        if dependencies.billing is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Billing is not configured.")
        return dependencies.billing

    @app.get("/v1/billing")
    def billing_state(authenticated_principal_id: str = Depends(principal_id)) -> dict:
        service = billing()
        # A payment left pending (a verify lost on the way) is settled here.
        service.recheck(authenticated_principal_id)
        entitlement = service.entitlement(authenticated_principal_id)
        sites = 0
        if dependencies.site_store is not None:
            sites = sum(
                1 for site in dependencies.site_store.list_for_tenant(authenticated_principal_id)
                if getattr(site.status, "value", site.status) != "removed"
            )
        now = dependencies.clock()
        return {
            "plan": entitlement.plan.value if entitlement.plan else None,
            "plan_name": service.plans[entitlement.plan].name if entitlement.plan else None,
            "active": entitlement.active,
            "ends_at": entitlement.ends_at.isoformat() if entitlement.ends_at else None,
            # Days begun count: a trial started a minute ago has 7 left, not 6.
            "days_left": -(-(entitlement.ends_at - now) // timedelta(days=1)) if entitlement.ends_at and entitlement.active else 0,
            "sites_used": sites,
            "sites_limit": entitlement.sites,
            "can_pay": service.can_take_payments,
            "changes": _changes_answer(_changes(dependencies, authenticated_principal_id, now)),
            "plans": [
                {
                    "plan": plan.plan_id.value, "name": plan.name, "sites": plan.sites, "price": plan.price,
                    "changes_a_month": plan.changes_a_month, "targets": plan.targets,
                    "rescan_days": plan.rescan_every.days, "best_writer": not plan.basic_writer,
                }
                for plan in service.plans.values()
                if plan.plan_id != PlanId.TRIAL
            ],
            "payments": [
                {
                    "plan": payment.plan.value, "amount": payment.amount, "status": payment.status.value,
                    "ref_id": payment.ref_id, "created_at": payment.created_at.isoformat(),
                }
                for payment in service.payments(authenticated_principal_id)
            ],
        }

    @app.post("/v1/billing/checkout")
    def checkout(request: CheckoutRequest, http_request: Request, authenticated_principal_id: str = Depends(principal_id)) -> dict:
        service = billing()
        mobile = None
        if dependencies.auth is not None:
            found = dependencies.auth.session_for(http_request.cookies.get(session_cookie))
            if found is not None:
                mobile = found[1].mobile
        try:
            url = service.checkout(authenticated_principal_id, request.plan, mobile=mobile)
        except PaymentUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"url": url}

    @app.get(CALLBACK_PATH, include_in_schema=False)
    def zarinpal_callback(Authority: str = "", Status: str = "") -> RedirectResponse:  # noqa: N803 - the gateway's names
        """Where the gateway sends the customer back; verified server to server before anything counts."""
        service = billing()
        outcome = "failed"
        if Authority and len(Authority) <= 100:
            payment = service.settle(authority=Authority, status=Status)
            if payment is not None:
                outcome = payment.status.value
        base = dependencies.dashboard_url.rstrip("/") or ""
        return RedirectResponse(f"{base}/#/billing?payment={outcome}", status_code=status.HTTP_303_SEE_OTHER)
