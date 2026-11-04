"""Signing up with a mobile number, the trial, the plan's limits, and paying by Zarinpal."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.accounts.otp import CODE_LIFETIME, CODES_PER_DAY, TRIES_PER_CODE, OtpService
from app.accounts.phone import normalize_mobile
from app.accounts.service import AttemptLimiter, AuthService
from app.accounts.store import AccountStore, SessionStore
from app.api.app import APIDependencies, create_app
from app.api.auth import APIKeyAuthenticator
from app.billing.models import PaymentStatus
from app.billing.plans import DEFAULT_PRICES, PERIOD, TRIAL_LENGTH, PlanId, catalog, parse_prices
from app.billing.service import Billing
from app.billing.store import PaymentStore, SubscriptionStore
from app.billing.zarinpal import GatewayError, ZarinpalGateway
from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
from app.notify.sms import LogSender, SmsError, SmsIrSender
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runtime.config import RuntimeConfig
from app.scan.store import SiteFileStore, SiteScanStore
from app.tenancy.store import TenantStore

NOW = datetime(2026, 11, 5, 9, tzinfo=timezone.utc)
MERCHANT = "00000000-0000-0000-0000-000000000000"
MOBILE = "09120000001"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FakeZarinpal:
    """The gateway's two calls, answering as told."""

    def __init__(self):
        self.calls = []
        self.verify_code = 100
        self.down = False

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self.down:
            raise GatewayError("URLError")
        if url.endswith("/request.json"):
            return {"data": {"code": 100, "authority": f"A{len(self.calls):035d}"}, "errors": []}
        return {"data": {"code": self.verify_code, "ref_id": 201, "card_pan": "6037-99**-****-1234"}, "errors": []}


class World:
    def __init__(self, *, signup=True, gateway=True):
        self.clock = Clock()
        self.repository = InMemoryRepository()
        self.sms = LogSender()
        self.sites = SiteStore(self.repository)
        tenants = TenantStore(self.repository)
        self.auth = AuthService(
            accounts=AccountStore(self.repository), sessions=SessionStore(self.repository), tenants=tenants, clock=self.clock
        )
        self.otp = OtpService(repository=self.repository, sender=self.sms, clock=self.clock)
        self.zarinpal = FakeZarinpal()
        self.billing = Billing(
            subscriptions=SubscriptionStore(self.repository),
            payments=PaymentStore(self.repository),
            plans=catalog(),
            gateway=ZarinpalGateway(merchant_id=MERCHANT, transport=self.zarinpal) if gateway else None,
            callback_url="https://hoshyarseo.ir/v1/billing/zarinpal/callback" if gateway else None,
            clock=self.clock,
        )
        app = create_app(APIDependencies(
            scheduler=JobScheduler(store=JobStore(self.repository), handlers=JobHandlerRegistry()),
            authenticator=APIKeyAuthenticator("unused-key", principal_id="nobody"),
            site_store=self.sites, auth=self.auth, otp=self.otp, billing=self.billing, signup_enabled=signup,
            scan_store=SiteScanStore(self.repository), file_store=SiteFileStore(self.repository),
            clock=self.clock, dashboard_url="/app/",
        ))
        self.client = TestClient(app, base_url="https://hoshyarseo.ir", follow_redirects=False)
        self.csrf = None

    def code(self):
        return self.sms.sent[-1][1]

    def ask(self, mobile=MOBILE, expect=202):
        response = self.client.post("/v1/auth/mobile/code", json={"mobile": mobile})
        assert response.status_code == expect, response.text
        return response

    def verify(self, code=None, expect=200, mobile=MOBILE, **details):
        response = self.client.post("/v1/auth/mobile/verify", json={"mobile": mobile, "code": code or self.code(), **details})
        assert response.status_code == expect, response.text
        if expect == 200 and "csrf_token" in response.json():
            self.csrf = response.json()["csrf_token"]
        return response.json()

    def sign_up(self, name="فروشگاه آزمایشی"):
        self.ask()
        return self.verify(business_name=name, accept_terms=True)

    def sign_in_again(self):
        """Days later the session has ended on its own (idle): in again with a code."""
        self.client.cookies.clear()
        self.ask()
        return self.verify()

    def post(self, path, body, expect):
        response = self.client.post(path, json=body, headers={"X-CSRF-Token": self.csrf})
        assert response.status_code == expect, response.text
        return response


# ---- numbers -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["09120000001", "+989120000001", "00989120000001", "989120000001", "9120000001", "۰۹۱۲ ۰۰۰ ۰۰۰۱", "0912-000-0001"],
)
def test_every_way_of_writing_a_number_is_one_number(typed):
    assert normalize_mobile(typed) == MOBILE


@pytest.mark.parametrize("typed", ["", "0212345678", "0912000000", "091200000011", "abc", "08120000001", "０９１２００００００１"])
def test_what_is_not_a_mobile_number_is_refused(typed):
    assert normalize_mobile(typed) is None


# ---- the code ------------------------------------------------------------------------


def otp(clock=None):
    sender = LogSender()
    return OtpService(repository=InMemoryRepository(), sender=sender, clock=clock or Clock()), sender


def test_a_code_proves_the_number_once():
    service, sender = otp()
    service.send(mobile=MOBILE, address="1.1.1.1")
    code = sender.sent[-1][1]
    assert len(code) == 6 and code.isdigit()
    assert service.check(mobile=MOBILE, code="۰" * 6 if code != "000000" else "111111", address="1.1.1.1") is False
    assert service.check(mobile=MOBILE, code=code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")), address="1.1.1.1")
    assert service.check(mobile=MOBILE, code=code, address="1.1.1.1") is False, "used"


def test_a_code_dies_after_two_minutes_and_five_tries():
    clock = Clock()
    service, sender = otp(clock)
    service.send(mobile=MOBILE, address="a")
    code = sender.sent[-1][1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(TRIES_PER_CODE):
        assert not service.check(mobile=MOBILE, code=wrong, address="a")
    assert not service.check(mobile=MOBILE, code=code, address="a"), "no sixth try, even with the right code"

    clock.now += timedelta(minutes=1)
    service.send(mobile=MOBILE, address="a")
    clock.now += CODE_LIFETIME
    assert not service.check(mobile=MOBILE, code=sender.sent[-1][1], address="a")


def test_codes_are_rationed_per_number_and_per_address():
    from app.accounts.otp import OtpError, OtpTooSoon

    clock = Clock()
    service, _ = otp(clock)
    service.send(mobile=MOBILE, address="a")
    with pytest.raises(OtpTooSoon):
        service.send(mobile=MOBILE, address="a")
    for _ in range(CODES_PER_DAY - 1):
        clock.now += timedelta(minutes=2)
        service.send(mobile=MOBILE, address="a")
    clock.now += timedelta(minutes=2)
    with pytest.raises(OtpError):
        service.send(mobile=MOBILE, address="a")

    limited = OtpService(
        repository=InMemoryRepository(), sender=LogSender(), clock=clock,
        per_address=AttemptLimiter(limit=2, window_seconds=3600),
    )
    limited.send(mobile="09120000002", address="b")
    limited.send(mobile="09120000003", address="b")
    with pytest.raises(OtpError):
        limited.send(mobile="09120000004", address="b")


def test_sms_ir_gets_the_template_and_the_key_in_a_header():
    seen = []

    def transport(url, headers, payload):
        seen.append((url, headers, payload))
        return 200, {"status": 1, "data": {"messageId": 1}}

    SmsIrSender(api_key="k", template_id=123, transport=transport).send_code(mobile=MOBILE, code="123456")
    url, headers, payload = seen[0]
    assert url == "https://api.sms.ir/v1/send/verify" and headers["X-API-KEY"] == "k" and "k" not in url
    assert payload == {"mobile": MOBILE, "templateId": 123, "parameters": [{"name": "CODE", "value": "123456"}]}

    with pytest.raises(SmsError):
        SmsIrSender(api_key="k", template_id=1, transport=lambda *a: (200, {"status": 0})).send_code(mobile=MOBILE, code="1")


# ---- signing up and in ---------------------------------------------------------------


def test_a_new_number_signs_up_with_a_name_and_the_terms_and_gets_a_trial():
    world = World()
    world.ask()
    code = world.code()
    assert world.verify(code)["status"] == "needs_signup", "the code alone does not make an account"
    signed = world.verify(code, business_name="فروشگاه آزمایشی", accept_terms=True)

    assert signed["mobile"] == MOBILE and signed["email"] is None and signed["method"] == "sms"
    tenant_id = signed["tenant_id"]
    entitlement = world.billing.entitlement(tenant_id)
    assert entitlement.plan == PlanId.TRIAL and entitlement.active and entitlement.ends_at == NOW + TRIAL_LENGTH
    assert world.client.get("/v1/auth/me").json()["tenant_id"] == tenant_id
    assert world.client.get("/v1/billing").json()["days_left"] == 7


def test_terms_not_accepted_make_no_account():
    world = World()
    world.ask()
    assert world.verify(business_name="x", accept_terms=False)["status"] == "needs_signup"
    assert world.auth.account_for_mobile(MOBILE) is None


def test_a_known_number_signs_in_with_its_code_alone():
    world = World()
    first = world.sign_up()
    world.client.cookies.clear()
    world.clock.now += timedelta(minutes=2)
    world.ask()
    again = world.verify()
    assert again["tenant_id"] == first["tenant_id"]


def test_a_wrong_code_is_refused_and_sign_up_can_be_closed():
    world = World()
    world.ask()
    world.verify(code="000000" if world.code() != "000000" else "111111", expect=401)

    closed = World(signup=False)
    closed.ask()  # answered the same, but nothing is sent
    closed.verify(code="123456", business_name="x", accept_terms=True, expect=401)
    assert closed.auth.account_for_mobile(MOBILE) is None


def test_a_number_that_is_not_one_is_refused_before_any_sms():
    world = World()
    world.ask(mobile="12345", expect=422)
    assert world.sms.sent == []


# ---- the plan's limits ---------------------------------------------------------------


def test_a_trial_has_room_for_one_site():
    world = World()
    world.sign_up()
    world.post("/v1/sites", {"name": "a", "base_url": "https://a.example/"}, 201)
    response = world.post("/v1/sites", {"name": "b", "base_url": "https://b.example/"}, 402)
    assert "no room for another site" in response.json()["detail"]


def test_an_ended_trial_starts_nothing_new_but_keeps_what_is_there():
    world = World()
    world.sign_up()
    site = world.post("/v1/sites", {"name": "a", "base_url": "https://a.example/"}, 201).json()
    world.clock.now += TRIAL_LENGTH + timedelta(minutes=1)
    world.sign_in_again()

    world.post(f"/v1/sites/{site['site_id']}/scans", {}, 402)
    assert world.client.get("/v1/sites").status_code == 200
    assert world.client.get("/v1/billing").json()["active"] is False


def test_a_tenant_set_up_by_hand_has_no_limits():
    world = World()
    entitlement = world.billing.entitlement("t-operator")
    assert entitlement.plan is None and entitlement.active and entitlement.sites is None


# ---- paying ---------------------------------------------------------------------------


def checkout(world, plan="starter"):
    url = world.post("/v1/billing/checkout", {"plan": plan}, 200).json()["url"]
    assert url.startswith("https://payment.zarinpal.com/pg/StartPay/")
    return url.rsplit("/", 1)[1]


def test_a_payment_adds_a_month_after_the_time_left():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)

    request_url, sent = world.zarinpal.calls[0]
    assert request_url.endswith("/pg/v4/payment/request.json")
    assert sent["amount"] == DEFAULT_PRICES[PlanId.STARTER] * 10 and sent["currency"] == "IRR"
    assert sent["metadata"]["mobile"] == MOBILE

    back = world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    assert back.status_code == 303 and back.headers["location"] == "/app/#/billing?payment=paid"
    _, verify = world.zarinpal.calls[1]
    assert verify == {"merchant_id": MERCHANT, "amount": DEFAULT_PRICES[PlanId.STARTER] * 10, "authority": authority}

    entitlement = world.billing.entitlement(tenant_id)
    assert entitlement.plan == PlanId.STARTER and entitlement.ends_at == NOW + TRIAL_LENGTH + PERIOD
    payments = world.client.get("/v1/billing").json()["payments"]
    assert payments[0]["status"] == "paid" and payments[0]["ref_id"] == "201"


def test_a_reloaded_return_page_counts_the_payment_once():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)
    for _ in range(3):
        world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    assert world.billing.entitlement(tenant_id).ends_at == NOW + TRIAL_LENGTH + PERIOD
    assert len(world.zarinpal.calls) == 2, "verified once"


@pytest.mark.parametrize(("status", "code"), [("NOK", 100), ("OK", -51)])
def test_a_cancelled_or_unpaid_payment_extends_nothing(status, code):
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)
    world.zarinpal.verify_code = code
    back = world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status={status}")
    assert back.headers["location"].endswith("payment=failed")
    assert world.billing.entitlement(tenant_id).plan == PlanId.TRIAL


def test_a_verify_that_could_not_be_made_stays_pending_and_is_asked_again():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)
    world.zarinpal.down = True
    assert world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK").headers["location"].endswith("payment=pending")
    world.zarinpal.down = False
    assert world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK").headers["location"].endswith("payment=paid")
    assert world.billing.entitlement(tenant_id).plan == PlanId.STARTER


def test_a_return_with_someone_elses_or_no_authority_changes_nothing():
    world = World()
    world.sign_up()
    assert world.client.get("/v1/billing/zarinpal/callback?Authority=Afake&Status=OK").headers["location"].endswith("payment=failed")
    assert world.client.get("/v1/billing/zarinpal/callback").headers["location"].endswith("payment=failed")
    assert len(world.zarinpal.calls) == 0


def test_an_ended_trial_can_still_pay_and_starts_from_today():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    world.clock.now += TRIAL_LENGTH + timedelta(days=3)
    world.sign_in_again()
    authority = checkout(world, plan="growth")
    world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    entitlement = world.billing.entitlement(tenant_id)
    assert entitlement.active and entitlement.sites == 3 and entitlement.ends_at == world.clock.now + PERIOD


def test_without_a_gateway_payment_says_it_is_not_available():
    world = World(gateway=False)
    world.sign_up()
    assert world.client.get("/v1/billing").json()["can_pay"] is False
    world.post("/v1/billing/checkout", {"plan": "starter"}, 503)


def test_the_trial_is_not_for_sale():
    world = World()
    world.sign_up()
    world.post("/v1/billing/checkout", {"plan": "trial"}, 503)


def test_a_payment_record_holds_no_more_than_the_gateway_masked():
    world = World()
    world.sign_up()
    authority = checkout(world)
    world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    payment, _ = world.billing._payments.by_authority(authority)
    assert payment.status == PaymentStatus.PAID and payment.card_pan == "6037-99**-****-1234"


# ---- settings -------------------------------------------------------------------------


def test_prices_are_a_setting_and_a_bad_one_stops_startup():
    assert parse_prices("starter=500000, pro=3000000")[PlanId.PRO] == 3_000_000
    with pytest.raises(ValueError):
        parse_prices("starter=10")
    with pytest.raises(ValueError):
        parse_prices("trial=1000")


@pytest.mark.parametrize(
    ("settings", "reason"),
    [
        ({"signup_enabled": True}, "SIGNUP_ENABLED needs SEO_AGENT_SMS_MODE"),
        ({"sms_mode": "log", "environment": " Production "}, "SMS_MODE=log writes codes"),
        ({"sms_mode": "smsir"}, "needs SEO_AGENT_SMS_API_KEY_REF"),
        ({"billing_mode": "zarinpal", "public_base_url": "https://hoshyarseo.ir"}, "ZARINPAL_MERCHANT_REF is required"),
        ({"billing_mode": "zarinpal", "zarinpal_merchant_ref": "M"}, "PUBLIC_BASE_URL is required"),
        (
            {"billing_mode": "zarinpal_sandbox", "zarinpal_merchant_ref": "M", "public_base_url": "https://hoshyarseo.ir", "environment": "PRODUCTION"},
            "takes no real money",
        ),
    ],
)
def test_half_configured_sign_up_or_billing_is_refused(settings, reason):
    with pytest.raises(ValueError, match=reason):
        RuntimeConfig(api_key="a-real-key", database_dsn="postgresql://x", **settings)


def test_billing_needs_the_public_address_but_not_google():
    config = RuntimeConfig(
        api_key="a-real-key", database_dsn="postgresql://x", billing_mode="zarinpal",
        zarinpal_merchant_ref="M", public_base_url="https://hoshyarseo.ir",
    )
    assert config.billing_mode == "zarinpal" and not config.google_sign_in_enabled


def test_the_weekly_rank_check_skips_a_tenant_whose_plan_ended():
    from app.models.sites import Site
    from app.models.target_keywords import KeywordStatus, TargetKeyword, TargetKeywordSet
    from app.targets.handler import RankTracker
    from app.targets.store import TargetKeywordStore

    repository = InMemoryRepository()
    sites, targets = SiteStore(repository), TargetKeywordStore(repository)
    sites.create(Site(site_id="s1", principal_id="t1", name="n", base_url="https://a.example/"))
    targets.save(TargetKeywordSet(site_id="s1", tenant_id="t1", keywords=[
        TargetKeyword(keyword="کفش", url="https://a.example/", status=KeywordStatus.CONFIRMED)
    ]), expected_version=0)

    class SERP:
        def search(self, *, query):
            raise AssertionError("a lapsed tenant must not cost a search")

    assert RankTracker(target_store=targets, site_store=sites, serp_provider=SERP(), active=lambda tenant: False).run_round() == {"checked": 0}


# ---- the review: money taken must never be left without its month -------------------


def test_a_verify_lost_on_the_way_is_settled_on_the_next_look_at_the_billing_page():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)
    world.zarinpal.down = True
    world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    world.zarinpal.down = False

    state = world.client.get("/v1/billing").json()

    assert state["plan"] == "starter" and state["payments"][0]["status"] == "paid"
    assert world.billing.entitlement(tenant_id).ends_at == NOW + TRIAL_LENGTH + PERIOD


def test_a_payment_nobody_came_back_from_is_settled_by_the_gateway_answer():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    checkout(world)  # paid at the bank, browser closed before the return
    world.client.get("/v1/billing")
    assert len(world.zarinpal.calls) == 1, "still under way: not asked yet"

    world.clock.now += timedelta(minutes=21)
    assert world.client.get("/v1/billing").json()["plan"] == "starter"
    assert world.billing.entitlement(tenant_id).ends_at == world.clock.now + (NOW + TRIAL_LENGTH - world.clock.now) + PERIOD

    second = World()
    second.sign_up()
    checkout(second)
    second.zarinpal.verify_code = -51
    second.clock.now += timedelta(minutes=21)
    assert second.client.get("/v1/billing").json()["payments"][0]["status"] == "failed"


def test_another_plan_takes_the_time_left_at_what_it_was_worth():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    world.clock.now += TRIAL_LENGTH + timedelta(days=1)
    world.sign_in_again()
    authority = checkout(world, plan="starter")
    world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    paid_at = world.clock.now
    world.clock.now += timedelta(days=10)  # 20 days of starter left
    world.sign_in_again()

    authority = checkout(world, plan="pro")
    world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")

    left = paid_at + PERIOD - world.clock.now
    worth = left * DEFAULT_PRICES[PlanId.STARTER] / DEFAULT_PRICES[PlanId.PRO]
    entitlement = world.billing.entitlement(tenant_id)
    assert entitlement.plan == PlanId.PRO
    assert entitlement.ends_at == world.clock.now + worth + PERIOD


def test_an_account_set_up_by_hand_has_nothing_to_buy():
    world = World()
    assert world.billing.can_take_payments
    from app.billing.service import PaymentUnavailable

    with pytest.raises(PaymentUnavailable):
        world.billing.checkout("t-operator", PlanId.STARTER)
    assert world.zarinpal.calls == []


def test_while_sign_up_is_closed_no_sms_goes_to_a_number_without_an_account():
    world = World(signup=False)
    world.ask()
    assert world.sms.sent == []


def test_a_kept_code_lives_long_enough_to_fill_in_the_sign_up_form():
    world = World()
    world.ask()
    code = world.code()
    assert world.verify(code)["status"] == "needs_signup"
    world.clock.now += timedelta(minutes=5)
    assert world.verify(code, business_name="فروشگاه", accept_terms=True)["mobile"] == MOBILE


def test_the_whole_service_stops_sending_past_its_hourly_cap():
    from app.accounts.otp import OtpError

    service = OtpService(
        repository=InMemoryRepository(), sender=LogSender(), clock=Clock(),
        all_sms=AttemptLimiter(limit=2, window_seconds=3600),
    )
    service.send(mobile="09120000002", address="a")
    service.send(mobile="09120000003", address="b")
    with pytest.raises(OtpError):
        service.send(mobile="09120000004", address="c")


def test_an_unexpected_gateway_refusal_keeps_the_payment_open():
    world = World()
    tenant_id = world.sign_up()["tenant_id"]
    authority = checkout(world)
    world.zarinpal.verify_code = -52  # an unexpected error, not "not paid"
    back = world.client.get(f"/v1/billing/zarinpal/callback?Authority={authority}&Status=OK")
    assert back.headers["location"].endswith("payment=pending")

    world.zarinpal.verify_code = 101  # it had gone through after all
    assert world.client.get("/v1/billing").json()["plan"] == "starter"
    assert world.billing.entitlement(tenant_id).plan == PlanId.STARTER
