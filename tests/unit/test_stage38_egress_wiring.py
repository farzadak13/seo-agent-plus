"""Egress, credentials and data freshness, wired from configuration.

Three separate things were left dangling after the transport and credential
layers were written, and each one is a real failure rather than tidiness:

* the transport existed but nothing built it from ``RuntimeConfig``, so the
  live path still used the old urllib call that threw the error body away;
* every fetch built its own credential, so one run minted four tokens;
* nothing stopped a run from asking for days Search Console has not settled,
  which reads as a traffic drop that never happened.
"""
from __future__ import annotations

from datetime import date, timedelta
import json

import pytest
from pydantic import ValidationError

from app.gsc.transport import GOOGLE_API_BASE, GoogleResponse
from app.ingestion.calendar import GSC_DATA_LAG_DAYS, latest_final_date
from app.models.sites import GSCConnectionConfig, SecretRef, Site, SiteStatus
from app.runtime.config import RuntimeConfig
from app.runtime.gsc import LiveGSCGateway, build_egress_config, build_google_transport
from app.runtime.service import PersistentSEORunService


def settings(**overrides) -> RuntimeConfig:
    base = {"api_key": "test-key", "database_dsn": "unused", "gsc_mode": "live"}
    return RuntimeConfig(**{**base, **overrides})


def site(auth_mode: str = "access_token") -> Site:
    return Site(
        site_id="s-1",
        principal_id="p-1",
        name="Example",
        base_url="https://example.com",
        status=SiteStatus.ACTIVE,
        gsc=GSCConnectionConfig(
            property_url="https://example.com",
            credential_ref=SecretRef(key="TOKEN"),
            auth_mode=auth_mode,
            row_limit=10,
        ),
    )


def empty_page() -> GoogleResponse:
    return GoogleResponse(200, json.dumps({"rows": []}).encode(), {})


# --- egress comes from configuration ----------------------------------------


def test_the_egress_config_is_built_from_the_runtime_config():
    config = build_egress_config(
        settings(
            google_proxy_url="http://127.0.0.1:8080",
            google_api_base="https://proxy.internal",
            gsc_timeout_seconds=12.5,
        )
    )

    assert config.proxy_url == "http://127.0.0.1:8080"
    assert config.base_url == "https://proxy.internal"
    assert config.timeout_seconds == 12.5


def test_the_default_egress_goes_straight_to_google():
    config = build_egress_config(settings())

    assert config.base_url == GOOGLE_API_BASE
    assert config.proxy_url is None
    assert config.verify_tls is True


def test_the_proxy_reaches_the_session_not_just_the_config():
    """A proxy that is configured but never applied fails only in production."""
    transport = build_google_transport(settings(google_proxy_url="http://127.0.0.1:8080"))

    assert transport.session.proxies["https"] == "http://127.0.0.1:8080"


def test_production_refuses_to_run_without_tls_verification():
    """Verification gets disabled to get through a local proxy and then ships."""
    with pytest.raises(ValidationError, match="VERIFY_TLS"):
        settings(environment="production", google_verify_tls=False)


def test_development_may_disable_tls_verification():
    assert settings(environment="development", google_verify_tls=False).google_verify_tls is False


# --- one credential per run, not one per fetch -------------------------------


class RecordingProviderFactory:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, *, kind, secret, session):
        self.calls.append({"kind": kind, "secret": secret, "session": session})
        return type("P", (), {"token": staticmethod(lambda: "minted")})()


def test_one_run_mints_one_token_not_one_per_fetch():
    """Four fetches per run; a provider per fetch is four token round trips."""
    factory = RecordingProviderFactory()
    gateway = LiveGSCGateway(site(), request_fn=lambda *a, **k: empty_page(), provider_factory=factory)

    for _ in range(4):
        gateway.fetch(
            site_id="s-1",
            property_url="https://example.com",
            credential="ya29.token",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )

    assert len(factory.calls) == 1


def test_a_changed_credential_builds_a_new_provider():
    """Caching must key on the credential, or a rotated secret is ignored."""
    factory = RecordingProviderFactory()
    gateway = LiveGSCGateway(site(), request_fn=lambda *a, **k: empty_page(), provider_factory=factory)

    for credential in ("first", "first", "second"):
        gateway.fetch(
            site_id="s-1",
            property_url="https://example.com",
            credential=credential,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )

    assert [call["secret"] for call in factory.calls] == ["first", "second"]


def test_the_credential_mints_through_the_same_session_as_the_data():
    """A token minted over a direct connection while data goes through a proxy
    is issued to a different address than the one that uses it."""
    factory = RecordingProviderFactory()
    transport = build_google_transport(settings(google_proxy_url="http://127.0.0.1:8080"))

    class SameSession:
        """A transport that answers locally but carries the real session."""

        session = transport.session

        def __call__(self, *args, **kwargs):
            return empty_page()

    gateway = LiveGSCGateway(site(), request_fn=SameSession(), provider_factory=factory)
    gateway.fetch(
        site_id="s-1",
        property_url="https://example.com",
        credential="ya29.token",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )

    assert factory.calls[0]["session"] is transport.session


def test_the_site_auth_mode_chooses_the_credential_kind():
    factory = RecordingProviderFactory()
    gateway = LiveGSCGateway(
        site("service_account"), request_fn=lambda *a, **k: empty_page(), provider_factory=factory
    )

    gateway.fetch(
        site_id="s-1",
        property_url="https://example.com",
        credential="{}",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )

    assert factory.calls[0]["kind"] == "service_account"


# --- the error body survives the live path ----------------------------------


def test_a_quota_403_is_retried_and_a_permission_403_is_not():
    """The old transport returned b'{}' for every error, which made these two
    the same failure. They have completely different fixes."""

    def error(reason):
        body = json.dumps(
            {"error": {"code": 403, "message": "denied", "errors": [{"reason": reason}]}}
        ).encode()
        return GoogleResponse(403, body, {})

    from app.gsc.client import GSCClientError

    attempts = {"count": 0}

    def quota_then_ok(*args, **kwargs):
        attempts["count"] += 1
        return error("quotaExceeded") if attempts["count"] == 1 else empty_page()

    gateway = LiveGSCGateway(
        site(), request_fn=quota_then_ok, provider_factory=RecordingProviderFactory()
    )
    gateway.fetch(
        site_id="s-1",
        property_url="https://example.com",
        credential="t",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )
    assert attempts["count"] == 2, "an exhausted quota clears on its own"

    denied = LiveGSCGateway(
        site(),
        request_fn=lambda *a, **k: error("forbidden"),
        provider_factory=RecordingProviderFactory(),
    )
    with pytest.raises(GSCClientError, match="permission_denied"):
        denied.fetch(
            site_id="s-1",
            property_url="https://example.com",
            credential="t",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )


# --- unsettled days are refused, not averaged in ----------------------------


def test_the_newest_usable_day_is_three_days_back():
    assert latest_final_date(date(2026, 9, 15)) == date(2026, 9, 12)
    assert GSC_DATA_LAG_DAYS == 3


def test_a_negative_lag_is_rejected_rather_than_reaching_into_the_future():
    with pytest.raises(ValueError):
        latest_final_date(date(2026, 9, 15), lag_days=-1)


def exploding_service(today, **config):
    def never(_site):
        raise AssertionError("the window must be rejected before any request")

    return PersistentSEORunService(
        repository=object(),
        secret_resolver=object(),
        settings=settings(**config),
        gateway_factory=never,
        today_fn=lambda: today,
    )


def run_window(service, *, start, end):
    return service.run(
        site=site(),
        start_date=start,
        end_date=end,
        normalized_url="https://example.com/page",
        normalized_query="seo",
        candidate_id="c-1",
    )


def test_a_window_reaching_into_unsettled_days_is_refused():
    """Recent days are still filling in. Comparing them against a settled
    baseline reports a drop that does not exist."""
    today = date(2026, 9, 15)
    service = exploding_service(today)

    with pytest.raises(ValueError) as raised:
        run_window(service, start=today - timedelta(days=6), end=today)

    assert "2026-09-12" in str(raised.value), "the message must name the newest usable day"


def test_a_window_ending_on_the_newest_settled_day_is_accepted():
    today = date(2026, 9, 15)
    service = exploding_service(today)

    with pytest.raises(AssertionError, match="before any request"):
        run_window(service, start=today - timedelta(days=9), end=date(2026, 9, 12))


def test_the_lag_is_configurable_for_a_property_that_settles_differently():
    today = date(2026, 9, 15)
    service = exploding_service(today, gsc_data_lag_days=1)

    with pytest.raises(AssertionError, match="before any request"):
        run_window(service, start=today - timedelta(days=5), end=date(2026, 9, 14))


# --- the runtime shares one transport ---------------------------------------


def test_the_service_reuses_one_transport_across_gateways():
    """A transport per run is a connection pool per run, and a second place
    where the proxy could be configured differently."""
    service = PersistentSEORunService(
        repository=object(), secret_resolver=object(), settings=settings()
    )

    first = service.gateway_factory(site())
    second = service.gateway_factory(site())

    assert first is not second
    assert first.request_fn is second.request_fn


def test_an_injected_transport_is_the_one_the_gateway_uses():
    sentinel = object()
    service = PersistentSEORunService(
        repository=object(), secret_resolver=object(), settings=settings(), transport=sentinel
    )

    assert service.gateway_factory(site()).request_fn is sentinel
