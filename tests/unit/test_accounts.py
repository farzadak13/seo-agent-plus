"""Dashboard sign-in: passwords, sessions, Google identity, and the API around them."""
import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.accounts.google_login import GoogleLogin, GoogleLoginError
from app.accounts.models import AccountStatus
from app.accounts.passwords import WeakPasswordError, hash_password, needs_rehash, verify_password
from app.accounts.service import (
    IDLE_TIMEOUT,
    SESSION_LIFETIME,
    AttemptLimiter,
    AuthService,
    SignInError,
    TooManyAttemptsError,
    token_hash,
)
from app.accounts.store import AccountExistsError, AccountStore, SessionStore
from app.models.tenants import Tenant, TenantStatus
from app.onboarding.google_oauth import GoogleOAuthConfig
from app.persistence.memory import InMemoryRepository
from app.tenancy.store import TenantStore

NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
PASSWORD = "a long enough passphrase"


class Clock:
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


def service(clock=None, repository=None):
    repository = repository or InMemoryRepository()
    clock = clock or Clock()
    tenants = TenantStore(repository)
    for tenant_id in ("t1", "t2"):
        tenants.create(Tenant(tenant_id=tenant_id, name=tenant_id))
    auth = AuthService(accounts=AccountStore(repository), sessions=SessionStore(repository),
                       tenants=tenants, clock=clock)
    return auth, repository, clock


# ---- passwords -----------------------------------------------------------------


def test_a_password_verifies_and_a_wrong_one_does_not():
    stored = hash_password(PASSWORD)
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    assert verify_password(PASSWORD, stored) and not verify_password("wrong passphrase!", stored)


def test_two_hashes_of_one_password_differ():
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_a_short_password_is_refused():
    with pytest.raises(WeakPasswordError):
        hash_password("short")


def test_no_stored_hash_never_verifies():
    assert verify_password(PASSWORD, None) is False


def test_old_parameters_are_noticed():
    assert needs_rehash("scrypt$1024$8$1$c2FsdA==$a2V5") is True
    assert needs_rehash(hash_password(PASSWORD)) is False


# ---- signing in and sessions -------------------------------------------------------


def test_sign_in_opens_a_session_whose_token_is_not_stored():
    auth, repository, _ = service()
    auth.create_account(tenant_id="t1", email="  Owner@Tennisino.com ", password=PASSWORD)

    token, session = auth.sign_in_with_password(email="owner@tennisino.com", password=PASSWORD, address="1.2.3.4")

    assert session.tenant_id == "t1" and session.token_hash == token_hash(token)
    assert all(token not in str(record.payload) for record in repository.list())
    assert auth.session_for(token)[1].email == "owner@tennisino.com"


@pytest.mark.parametrize("email, password", [("owner@tennisino.com", "wrong passphrase!"), ("nobody@x.com", PASSWORD)])
def test_every_refusal_says_the_same_thing(email, password):
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="owner@tennisino.com", password=PASSWORD)
    with pytest.raises(SignInError) as raised:
        auth.sign_in_with_password(email=email, password=password, address="1.2.3.4")
    assert str(raised.value) == "The email or password is not correct."


def test_a_disabled_account_cannot_sign_in_and_its_sessions_stop():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    store = AccountStore(repository)
    store.update(store.get(account.account_id).model_copy(update={"status": AccountStatus.DISABLED}))
    assert auth.session_for(token) is None
    with pytest.raises(SignInError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")


def test_repeated_failures_are_slowed_down_per_email():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    for _ in range(8):
        with pytest.raises(SignInError):
            auth.sign_in_with_password(email="a@x.com", password="wrong passphrase!", address="1")
    with pytest.raises(TooManyAttemptsError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="2")


def test_the_limiter_forgets_after_its_window():
    now = {"t": 0.0}
    limiter = AttemptLimiter(limit=2, window_seconds=60, clock=lambda: now["t"])
    limiter.failed("k")
    limiter.failed("k")
    assert limiter.blocked("k")
    now["t"] = 61
    assert not limiter.blocked("k")


def test_a_session_ends_after_its_lifetime_even_if_used():
    auth, _, clock = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    for day in range(1, 14):
        clock.now = NOW + timedelta(days=day)
        assert auth.session_for(token) is not None
    clock.now = NOW + SESSION_LIFETIME
    assert auth.session_for(token) is None


def test_an_idle_session_ends():
    auth, _, clock = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    clock.now = NOW + IDLE_TIMEOUT
    assert auth.session_for(token) is None


def test_signing_out_ends_the_session():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    auth.sign_out(token)
    assert auth.session_for(token) is None


def test_two_accounts_cannot_share_an_email():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    with pytest.raises(AccountExistsError):
        auth.create_account(tenant_id="t2", email="A@X.com", password=PASSWORD)


# ---- Google identity -----------------------------------------------------------------


def test_google_links_once_by_verified_email_then_by_its_own_id():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="owner@tennisino.com", password=None)
    _, first = auth.sign_in_with_google(subject="g-1", email="Owner@tennisino.com", email_verified=True)
    # Later the Google email changes; the link holds by Google's own id.
    _, again = auth.sign_in_with_google(subject="g-1", email="new@gmail.com", email_verified=True)
    assert first.tenant_id == again.tenant_id == "t1"


@pytest.mark.parametrize(
    "subject, email, verified",
    [("g-9", "stranger@gmail.com", True), ("g-9", "owner@tennisino.com", False), ("g-9", None, True)],
)
def test_google_cannot_create_or_claim_an_account(subject, email, verified):
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="owner@tennisino.com", password=None)
    with pytest.raises(SignInError):
        auth.sign_in_with_google(subject=subject, email=email, email_verified=verified)


def test_an_account_linked_to_one_google_identity_refuses_another():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="owner@tennisino.com", password=None)
    auth.sign_in_with_google(subject="g-1", email="owner@tennisino.com", email_verified=True)
    with pytest.raises(SignInError):
        auth.sign_in_with_google(subject="g-2", email="owner@tennisino.com", email_verified=True)


def id_token(claims):
    return "h." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".s"


CONFIG = GoogleOAuthConfig(client_id="c", client_secret="s", public_base_url="https://hoshyarseo.ir")


def google_login(claims=None, status=200, clock=None):
    forms = []

    def exchange(form):
        forms.append(form)
        return status, {"id_token": id_token(claims or {"sub": "g-1", "email": "a@x.com", "email_verified": True})}

    return GoogleLogin(config=CONFIG, repository=InMemoryRepository(), exchange=exchange,
                       clock=clock or Clock()), forms


def test_google_login_asks_only_who_the_person_is():
    login, _ = google_login()
    url, nonce = login.start()
    query = parse_qs(urlparse(url).query)
    assert query["scope"] == ["openid email"]
    assert query["redirect_uri"] == ["https://hoshyarseo.ir/v1/auth/google/callback"]
    assert query["code_challenge_method"] == ["S256"] and nonce


def test_google_login_is_bound_to_the_browser_and_single_use():
    login, forms = google_login()
    url, nonce = login.start()
    state = parse_qs(urlparse(url).query)["state"][0]
    with pytest.raises(GoogleLoginError, match="another browser"):
        login.complete(state_id=state, code="c", error=None, browser_nonce="not-it")
    url, nonce = login.start()
    state = parse_qs(urlparse(url).query)["state"][0]
    identity = login.complete(state_id=state, code="c", error=None, browser_nonce=nonce)
    assert (identity.subject, identity.email_verified) == ("g-1", True)
    assert forms[-1]["code_verifier"]
    with pytest.raises(GoogleLoginError, match="already used"):
        login.complete(state_id=state, code="c", error=None, browser_nonce=nonce)


def test_a_slow_google_login_expires():
    clock = Clock()
    login, _ = google_login(clock=clock)
    url, nonce = login.start()
    clock.now = NOW + timedelta(minutes=11)
    with pytest.raises(GoogleLoginError, match="too long"):
        login.complete(state_id=parse_qs(urlparse(url).query)["state"][0], code="c", error=None, browser_nonce=nonce)


# ---- the API ---------------------------------------------------------------------------


def api(*, origins=(), login=None):
    from app.api.app import APIDependencies, create_app
    from app.api.auth import APIKeyAuthenticator
    from app.jobs import JobHandlerRegistry, JobScheduler, JobStore
    from app.models.sites import Site
    from app.onboarding.site_store import SiteStore

    auth, repository, clock = service()
    auth.create_account(tenant_id="t1", email="owner@tennisino.com", password=PASSWORD)
    sites = SiteStore(repository)
    sites.create(Site(site_id="s1", principal_id="t1", name="T", base_url="https://tennisino.com/"))
    sites.create(Site(site_id="s2", principal_id="t2", name="O", base_url="https://other.ir/"))
    app = create_app(APIDependencies(
        scheduler=JobScheduler(store=JobStore(repository), handlers=JobHandlerRegistry()),
        authenticator=APIKeyAuthenticator("operator-key", principal_id="t1"),
        site_store=sites, auth=auth, google_login=login, dashboard_url="/dashboard/",
        allowed_origins=origins, tenant_name=lambda tenant_id: "Tennisino Shop",
    ))
    return TestClient(app, base_url="https://hoshyarseo.ir"), auth


def sign_in(client):
    response = client.post("/v1/auth/login", json={"email": "owner@tennisino.com", "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response


def test_signing_in_sets_a_protected_cookie_and_returns_the_csrf_token():
    client, _ = api()
    response = sign_in(client)
    cookie = response.headers["set-cookie"]
    for flag in ("hoshyarseo_session=", "HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert flag.lower() in cookie.lower()
    body = response.json()
    assert body["tenant_name"] == "Tennisino Shop" and body["csrf_token"]
    assert PASSWORD not in response.text, "the password itself never comes back"


def test_the_cookie_reads_its_own_tenants_data_only():
    client, _ = api()
    sign_in(client)
    assert client.get("/v1/sites/s1").status_code == 200
    assert client.get("/v1/sites/s2").status_code == 404


def test_a_change_needs_the_csrf_token_as_well_as_the_cookie():
    client, _ = api()
    csrf = sign_in(client).json()["csrf_token"]
    body = {"name": "New", "base_url": "https://new.example/"}
    assert client.post("/v1/sites", json=body).status_code == 403
    assert client.post("/v1/sites", json=body, headers={"X-CSRF-Token": "guess"}).status_code == 403
    assert client.post("/v1/sites", json=body, headers={"X-CSRF-Token": csrf}).status_code == 201


def test_the_api_key_still_works_without_a_cookie():
    client, _ = api()
    assert client.get("/v1/sites/s1", headers={"Authorization": "Bearer operator-key"}).status_code == 200


def test_without_a_session_nothing_is_readable():
    client, _ = api()
    assert client.get("/v1/sites/s1").status_code == 401
    client.cookies.set("hoshyarseo_session", "made-up")
    assert client.get("/v1/sites/s1").status_code == 401


def test_me_and_sign_out():
    client, _ = api()
    csrf = sign_in(client).json()["csrf_token"]
    assert client.get("/v1/auth/me").json()["email"] == "owner@tennisino.com"
    assert client.post("/v1/auth/logout").status_code == 403, "sign-out needs the token too"
    assert client.post("/v1/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/v1/auth/me").status_code == 401


def test_a_wrong_password_is_401_without_detail():
    client, _ = api()
    response = client.post("/v1/auth/login", json={"email": "owner@tennisino.com", "password": "wrong passphrase!"})
    assert response.status_code == 401
    assert response.json()["detail"] == "The email or password is not correct."


def test_another_site_cannot_sign_a_visitor_in():
    client, _ = api()
    response = client.post(
        "/v1/auth/login", json={"email": "owner@tennisino.com", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_a_listed_dashboard_origin_may_sign_in_and_is_allowed_by_cors():
    client, _ = api(origins=("https://app.hoshyarseo.ir",))
    headers = {"Origin": "https://app.hoshyarseo.ir"}
    response = client.post("/v1/auth/login", json={"email": "owner@tennisino.com", "password": PASSWORD},
                           headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.hoshyarseo.ir"
    assert response.headers["access-control-allow-credentials"] == "true"
    other = client.get("/v1/auth/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in other.headers


def test_google_sign_in_end_to_end_lands_on_the_dashboard_signed_in():
    login, _ = google_login({"sub": "g-1", "email": "owner@tennisino.com", "email_verified": True})
    client, _ = api(login=login)
    start = client.get("/v1/auth/google/start", follow_redirects=False)
    assert start.status_code == 302 and start.headers["location"].startswith("https://accounts.google.com/")
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]

    back = client.get(f"/v1/auth/google/callback?state={state}&code=c", follow_redirects=False)

    assert back.status_code == 303 and back.headers["location"] == "/dashboard/"
    assert client.get("/v1/auth/me").json()["method"] == "google"


def test_google_sign_in_for_a_stranger_is_refused_with_a_page():
    login, _ = google_login({"sub": "g-9", "email": "stranger@gmail.com", "email_verified": True})
    client, _ = api(login=login)
    start = client.get("/v1/auth/google/start", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    back = client.get(f"/v1/auth/google/callback?state={state}&code=c", follow_redirects=False)
    assert back.status_code == 400 and 'dir="rtl"' in back.text
    assert client.get("/v1/auth/me").status_code == 401


# ---- the review's blockers: stale writes and sessions that outlive the cure --------


def test_a_write_from_a_stale_copy_is_refused():
    from app.accounts.store import AccountChangedError

    auth, repository, _ = service()
    store = AccountStore(repository)
    stale = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    store.update(stale.model_copy(update={"status": AccountStatus.DISABLED}))  # the operator
    with pytest.raises(AccountChangedError):
        store.update(stale.model_copy(update={"last_login_at": NOW}))  # a sign-in's stale copy
    assert store.get(stale.account_id).status == AccountStatus.DISABLED


def test_disabling_during_a_sign_in_is_not_undone(monkeypatch):
    # The operator disables the account while the sign-in is hashing.
    import app.accounts.service as service_module

    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    real_verify = service_module.verify_password

    def verify_then_disable(password, stored):
        result = real_verify(password, stored)
        store = AccountStore(repository)
        store.update(store.get(account.account_id).model_copy(update={"status": AccountStatus.DISABLED}))
        return result

    monkeypatch.setattr(service_module, "verify_password", verify_then_disable)
    with pytest.raises(SignInError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    assert AccountStore(repository).get(account.account_id).status == AccountStatus.DISABLED


def test_disable_then_enable_does_not_bring_an_old_session_back():
    auth, repository, clock = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    stolen, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    clock.now = NOW + timedelta(minutes=1)

    account = auth.end_sessions(AccountStore(repository).get(account.account_id), status=AccountStatus.DISABLED)
    AccountStore(repository).update(account.model_copy(update={"status": AccountStatus.ACTIVE}))

    assert auth.session_for(stolen) is None
    clock.now = NOW + timedelta(minutes=2)
    fresh, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    assert auth.session_for(fresh) is not None, "a session opened afterwards works"


def test_a_new_password_ends_every_earlier_session():
    auth, repository, clock = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    old, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    clock.now = NOW + timedelta(minutes=1)
    auth.end_sessions(AccountStore(repository).get(account.account_id), password_hash=hash_password("another long passphrase"))
    assert auth.session_for(old) is None


def test_linking_google_survives_a_concurrent_change():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    store = AccountStore(repository)
    store.update(store.get(account.account_id).model_copy(update={"last_login_at": NOW}))  # stale `account` now
    linked = store.bind_google(account, "g-1")
    assert linked.google_subject == "g-1" and linked.last_login_at == NOW


# ---- the second review: the generation race, limits, linking, pages and config ----------


def test_an_operator_change_landing_while_the_session_is_written_revokes_it(monkeypatch):
    # The change lands after the session exists but before the account is read again.
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    store = AccountStore(repository)
    sessions = auth._sessions
    real_create = sessions.create
    opened = []

    def create_then_revoke_all(session):
        opened.append(real_create(session))
        auth.end_sessions(store.get(account.account_id))
        return opened[-1]

    monkeypatch.setattr(sessions, "create", create_then_revoke_all)
    with pytest.raises(SignInError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    stored, _ = sessions.get(opened[0].token_hash)
    assert stored.revoked_at is not None


def test_an_operator_change_landing_after_the_sign_in_ends_the_session():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, session = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    assert auth.session_for(token) is not None
    after = auth.end_sessions(AccountStore(repository).get(account.account_id))
    assert after.session_generation == session.generation + 1
    assert auth.session_for(token) is None


def test_looking_a_key_up_never_stores_it():
    limiter = AttemptLimiter(limit=3, window_seconds=60)
    for n in range(100):
        assert limiter.blocked(f"random-{n}@x.com") is False
    assert len(limiter) == 0


def test_a_key_whose_events_aged_out_is_dropped():
    now = {"t": 0.0}
    limiter = AttemptLimiter(limit=3, window_seconds=60, clock=lambda: now["t"])
    limiter.record("k")
    now["t"] = 61
    assert limiter.blocked("k") is False and len(limiter) == 0


def test_the_limiter_never_holds_more_than_its_maximum_keys():
    now = {"t": 0.0}
    limiter = AttemptLimiter(limit=3, window_seconds=600, clock=lambda: now["t"], max_keys=5)
    for n in range(20):
        now["t"] = n
        limiter.record(f"k{n}")
    assert len(limiter) == 5
    assert limiter._events.keys() == {f"k{n}" for n in range(15, 20)}, "the oldest keys went first"


def test_one_address_trying_many_emails_is_slowed_down():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    for n in range(40):
        with pytest.raises(SignInError):
            auth.sign_in_with_password(email=f"guess{n}@x.com", password=PASSWORD, address="9.9.9.9")
    with pytest.raises(TooManyAttemptsError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="9.9.9.9")
    auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="8.8.8.8")


def test_an_old_hash_is_replaced_on_the_next_sign_in():
    from app.accounts.passwords import _derive

    salt = b"0123456789abcdef"
    old = "$".join(["scrypt", "1024", "8", "1", base64.urlsafe_b64encode(salt).decode(),
                    base64.urlsafe_b64encode(_derive(PASSWORD, salt, 1024, 8, 1)).decode()])
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    store = AccountStore(repository)
    store.update(store.get(account.account_id).model_copy(update={"password_hash": old}))

    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")

    renewed = store.get(account.account_id).password_hash
    assert renewed != old and not needs_rehash(renewed) and verify_password(PASSWORD, renewed)
    assert auth.session_for(token) is not None, "the rehash does not cost the sign-in its session"


def test_a_write_the_repository_refuses_is_reported_as_a_changed_account(monkeypatch):
    from app.accounts.store import AccountChangedError
    from app.persistence.contracts import PersistenceConflictError

    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)

    def refuse(record, *, expected_version):
        raise PersistenceConflictError("someone else wrote version 2 first")

    monkeypatch.setattr(repository, "replace", refuse)
    with pytest.raises(AccountChangedError):
        AccountStore(repository).update(account.model_copy(update={"status": AccountStatus.DISABLED}))


def test_unlinking_google_ends_its_sessions_and_it_does_not_link_itself_again():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    token, _ = auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    store = AccountStore(repository)

    unlinked = auth.unlink_google(store.get(account.account_id))

    assert unlinked.google_subject is None and not unlinked.google_link_allowed
    assert store.by_google_subject("g-1") is None
    assert auth.session_for(token) is None
    for subject in ("g-1", "g-2"):  # neither the cut-off identity nor another with the same email
        with pytest.raises(SignInError):
            auth.sign_in_with_google(subject=subject, email="a@x.com", email_verified=True)
    assert store.by_google_subject("g-1") is None and store.by_google_subject("g-2") is None


def test_the_operator_can_allow_google_linking_again():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    store = AccountStore(repository)
    auth.unlink_google(store.get(account.account_id))

    auth.allow_google(store.get(account.account_id))

    again, _ = auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    assert auth.session_for(again)[1].google_subject == "g-1"


def test_google_can_be_turned_off_before_it_was_ever_linked():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    store = AccountStore(repository)
    auth.unlink_google(store.get(account.account_id))
    with pytest.raises(SignInError):
        auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")  # the password still works


# ---- a suspended customer is shut out of the dashboard too ------------------------------


def suspend(repository, tenant_id="t1", status=TenantStatus.SUSPENDED):
    tenants = TenantStore(repository)
    tenants.update(tenants.get(tenant_id).model_copy(update={"status": status}))


def test_a_suspended_tenants_sessions_stop_and_it_cannot_sign_in():
    auth, repository, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")

    suspend(repository)

    assert auth.session_for(token) is None
    with pytest.raises(SignInError, match="suspended"):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    with pytest.raises(SignInError, match="email or password"):
        auth.sign_in_with_password(email="a@x.com", password="wrong passphrase!", address="1")
    suspend(repository, status=TenantStatus.ACTIVE)
    assert auth.session_for(token) is not None, "suspension is reversible, like the tenant's API keys"


def test_a_suspended_tenants_account_cannot_sign_in_with_google_or_collect_an_identity():
    auth, repository, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    auth.create_account(tenant_id="t1", email="b@x.com", password=None)
    linked, _ = auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    suspend(repository)

    with pytest.raises(SignInError, match="suspended"):
        auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    with pytest.raises(SignInError, match="suspended"):
        auth.sign_in_with_google(subject="g-2", email="b@x.com", email_verified=True)
    assert AccountStore(repository).by_google_subject("g-2") is None
    assert auth.session_for(linked) is None


def test_an_account_whose_tenant_is_gone_cannot_sign_in():
    auth, _, _ = service()
    auth.create_account(tenant_id="t-gone", email="a@x.com", password=PASSWORD)
    with pytest.raises(SignInError, match="suspended"):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")


def test_a_suspended_tenants_cookie_is_refused_by_the_api():
    client, auth = api()
    csrf = sign_in(client).json()["csrf_token"]
    suspend(auth._accounts._repository)
    assert client.get("/v1/sites/s1").status_code == 401
    body = {"name": "New", "base_url": "https://new.example/"}
    assert client.post("/v1/sites", json=body, headers={"X-CSRF-Token": csrf}).status_code == 401
    refused = client.post("/v1/auth/login", json={"email": "owner@tennisino.com", "password": PASSWORD})
    assert refused.status_code == 401 and "suspended" in refused.json()["detail"]


# ---- counting attempts per client, IPv6 by its /64 --------------------------------------


@pytest.mark.parametrize("address, key", [
    ("203.0.113.7", "203.0.113.7"),
    ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
    ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
    ("::ffff:203.0.113.7", "203.0.113.7"),
    ("testclient", "testclient"),
])
def test_an_address_is_counted_by_the_right_key(address, key):
    from app.accounts.service import address_key

    assert address_key(address) == key


def test_one_ipv6_subscriber_cannot_dodge_the_limit_by_changing_address():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    for n in range(40):
        with pytest.raises(SignInError):
            auth.sign_in_with_password(email=f"guess{n}@x.com", password=PASSWORD, address=f"2001:db8:1:2::{n + 1:x}")
    with pytest.raises(TooManyAttemptsError):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="2001:db8:1:2:ffff::9")
    auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="2001:db8:1:3::1")  # the next /64


def test_google_starts_from_one_ipv6_subscriber_count_together():
    login = GoogleLogin(config=CONFIG, repository=InMemoryRepository(), exchange=lambda form: (500, {}),
                        starts_per_address=AttemptLimiter(limit=2, window_seconds=600))
    login.start("2001:db8:1:2::1")
    login.start("2001:db8:1:2::2")
    with pytest.raises(GoogleLoginError, match="Too many"):
        login.start("2001:db8:1:2::3")
    login.start("2001:db8:9:9::1")


def test_an_unlinked_google_identity_can_go_to_another_account():
    auth, repository, _ = service()
    first = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    auth.create_account(tenant_id="t2", email="b@x.com", password=None)
    store = AccountStore(repository)
    store.bind_google(first, "g-1")
    auth.unlink_google(store.get(first.account_id))
    assert store.bind_google(store.by_email("b@x.com"), "g-1").google_subject == "g-1"
    assert store.by_google_subject("g-1").email == "b@x.com"


def test_a_disabled_account_does_not_collect_a_google_identity():
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    store = AccountStore(repository)
    auth.end_sessions(store.get(account.account_id), status=AccountStatus.DISABLED)
    with pytest.raises(SignInError):
        auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)
    assert store.get(account.account_id).google_subject is None and store.by_google_subject("g-1") is None


def test_starting_google_sign_in_is_limited_per_address():
    login = GoogleLogin(config=CONFIG, repository=InMemoryRepository(), exchange=lambda form: (500, {}),
                        starts_per_address=AttemptLimiter(limit=2, window_seconds=600))
    login.start("1.2.3.4")
    login.start("1.2.3.4")
    with pytest.raises(GoogleLoginError, match="Too many"):
        login.start("1.2.3.4")
    login.start("5.6.7.8")


def test_too_many_google_starts_get_a_page_with_the_way_back():
    login = GoogleLogin(config=CONFIG, repository=InMemoryRepository(), exchange=lambda form: (500, {}),
                        starts_per_address=AttemptLimiter(limit=1, window_seconds=600))
    client, _ = api(login=login)
    assert client.get("/v1/auth/google/start", follow_redirects=False).status_code == 302
    refused = client.get("/v1/auth/google/start", follow_redirects=False)
    assert refused.status_code == 400 and 'href="/dashboard/"' in refused.text


def test_a_failed_google_sign_in_page_leads_back_to_the_dashboard():
    login, _ = google_login()
    client, _ = api(login=login)
    page = client.get("/v1/auth/google/callback?state=made-up&code=c", follow_redirects=False)
    assert page.status_code == 400
    assert "ورود انجام نشد" in page.text and 'href="/dashboard/"' in page.text
    assert "not valid" in page.text


def test_a_page_with_a_null_origin_cannot_sign_a_visitor_in():
    client, _ = api(origins=("https://app.hoshyarseo.ir",))
    response = client.post("/v1/auth/login", json={"email": "owner@tennisino.com", "password": PASSWORD},
                           headers={"Origin": "null"})
    assert response.status_code == 403


def test_the_browser_preflight_is_answered_for_the_dashboard_only():
    client, _ = api(origins=("https://app.hoshyarseo.ir",))
    ask = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type,x-csrf-token"}
    allowed = client.options("/v1/sites", headers={"Origin": "https://app.hoshyarseo.ir", **ask})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.hoshyarseo.ir"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "x-csrf-token" in allowed.headers["access-control-allow-headers"].lower()
    refused = client.options("/v1/sites", headers={"Origin": "https://evil.example", **ask})
    assert "access-control-allow-origin" not in refused.headers


@pytest.mark.parametrize("origin", ["*", "null", "https://app.hoshyarseo.ir/", "https://app.hoshyarseo.ir/x",
                                    "https://*.hoshyarseo.ir", "app.hoshyarseo.ir", "ftp://app.hoshyarseo.ir"])
def test_a_dashboard_origin_must_be_exact(origin):
    from app.runtime.config import RuntimeConfig

    with pytest.raises(ValueError, match="SEO_AGENT_DASHBOARD_ORIGINS"):
        RuntimeConfig(api_key="k", database_dsn="d", dashboard_origins=(origin,))


def test_real_dashboard_origins_are_accepted_and_production_wants_https():
    from app.runtime.config import RuntimeConfig

    config = RuntimeConfig(api_key="k", database_dsn="d",
                           dashboard_origins=("https://app.hoshyarseo.ir", "http://localhost:5173"))
    assert tuple(config.dashboard_origins) == ("https://app.hoshyarseo.ir", "http://localhost:5173")
    with pytest.raises(ValueError, match="https in production"):
        RuntimeConfig(api_key="k", database_dsn="d", environment="production",
                      dashboard_origins=("http://localhost:5173",))


# ---- the fourth review: linking decided on a fresh read; attempts counted up front ------


def _turn_google_off(auth, store, account_id):
    auth.unlink_google(store.get(account_id))


def _disable(auth, store, account_id):
    auth.end_sessions(store.get(account_id), status=AccountStatus.DISABLED)


def _link_another(auth, store, account_id):
    store.bind_google(store.get(account_id), "g-other")


@pytest.mark.parametrize("operator_change", [_turn_google_off, _disable, _link_another])
def test_an_operator_change_landing_just_before_the_link_is_not_undone(monkeypatch, operator_change):
    # The sign-in has checked its copy of the account; the change lands before it links.
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=None)
    store = auth._accounts
    real_bind = store.bind_google

    def change_then_bind(stale, subject):
        operator_change(auth, AccountStore(repository), account.account_id)
        return real_bind(stale, subject)

    monkeypatch.setattr(store, "bind_google", change_then_bind)
    with pytest.raises(SignInError):
        auth.sign_in_with_google(subject="g-1", email="a@x.com", email_verified=True)

    assert store.by_google_subject("g-1") is None, "the identity was not linked"
    assert store.get(account.account_id).google_subject != "g-1"


def test_an_attempt_is_counted_in_one_step():
    limiter = AttemptLimiter(limit=2, window_seconds=60)
    assert limiter.attempt("k") and limiter.attempt("k")
    assert limiter.attempt("k") is False and len(limiter._events["k"]) == 2, "a refused attempt is not counted"
    limiter.cancel("k")
    assert limiter.attempt("k") is True
    limiter.cancel("k")
    limiter.cancel("k")
    assert len(limiter) == 0


def test_guesses_sent_all_at_once_still_meet_the_limit(monkeypatch):
    # Each guess holds its thread inside the (slow) password check until all have arrived.
    import threading
    import time

    import app.accounts.service as service_module

    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    gate, lock = threading.Event(), threading.Lock()
    outcome = {"inside": 0, "too_many": 0}

    def slow_check(password, stored):
        with lock:
            outcome["inside"] += 1
        gate.wait(5)
        return False

    monkeypatch.setattr(service_module, "verify_password", slow_check)

    def guess(n):
        try:
            auth.sign_in_with_password(email="a@x.com", password=f"guess number {n}", address=f"10.0.0.{n}")
        except TooManyAttemptsError:
            with lock:
                outcome["too_many"] += 1
        except SignInError:
            pass

    threads = [threading.Thread(target=guess, args=(n,)) for n in range(12)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while outcome["inside"] + outcome["too_many"] < 12 and time.monotonic() < deadline:
        time.sleep(0.01)
    gate.set()
    for thread in threads:
        thread.join()
    assert outcome == {"inside": 8, "too_many": 4}


def test_a_right_password_is_not_held_against_the_address():
    auth, _, _ = service()
    auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    for _ in range(45):
        auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1.2.3.4")


def test_a_rehash_the_policy_now_refuses_does_not_cost_the_sign_in(monkeypatch):
    import app.accounts.service as service_module

    salt = b"0123456789abcdef"
    from app.accounts.passwords import _derive

    old = "$".join(["scrypt", "1024", "8", "1", base64.urlsafe_b64encode(salt).decode(),
                    base64.urlsafe_b64encode(_derive(PASSWORD, salt, 1024, 8, 1)).decode()])
    auth, repository, _ = service()
    account = auth.create_account(tenant_id="t1", email="a@x.com", password=PASSWORD)
    store = AccountStore(repository)
    store.update(store.get(account.account_id).model_copy(update={"password_hash": old}))

    def stricter_policy(password):
        raise WeakPasswordError("A password needs at least 40 characters.")

    monkeypatch.setattr(service_module, "hash_password", stricter_policy)
    token, _ = auth.sign_in_with_password(email="a@x.com", password=PASSWORD, address="1")
    assert auth.session_for(token) is not None
    assert store.get(account.account_id).password_hash == old, "kept until a rehash can succeed"


def test_the_sign_in_method_is_one_of_the_known_ones():
    from pydantic import ValidationError

    from app.api.schemas import SignedIn

    fields = dict(email="a@x.com", tenant_id="t1", tenant_name="T", csrf_token="c", expires_at=NOW)
    assert SignedIn(method="google", **fields).method == "google"
    with pytest.raises(ValidationError):
        SignedIn(method="magic-link", **fields)
