"""The live Search Console gateway: one transport, one credential, per run.

Two things changed here and both were found by calling the real API rather
than reasoning about it.

**Errors keep their body.** The previous transport turned every HTTP error
into ``b"{}"``, which made a wrong token (401), an account that was never
granted the property (403) and a project that ran out of quota (403 with
``reason=quotaExceeded``) indistinguishable. Those have three different fixes
and only the body says which one happened. ``GoogleTransport`` keeps it.

**A credential is a provider, not a string.** A pasted access token dies after
an hour, and a run that outlives it fails halfway through with a 401 that
reads like a permission problem. The provider refreshes. It is built once per
credential and cached, because a single run makes four fetches and each one
would otherwise mint its own token.
"""
from __future__ import annotations

from app.gsc.client import GSCClient
from app.gsc.config import GSCClientConfig
from app.gsc.credentials import build_token_provider
from app.gsc.transport import EgressConfig, GoogleTransport


def build_egress_config(settings) -> EgressConfig:
    """Egress settings for one runtime, in one place."""
    return EgressConfig(
        base_url=settings.google_api_base,
        proxy_url=settings.google_proxy_url,
        timeout_seconds=settings.gsc_timeout_seconds,
        verify_tls=settings.google_verify_tls,
    )


def build_google_transport(settings) -> GoogleTransport:
    """One transport — and therefore one connection pool — per runtime."""
    return GoogleTransport(build_egress_config(settings))


class LiveGSCGateway:
    def __init__(self, site, *, timeout=30, request_fn=None, provider_factory=build_token_provider):
        self.site = site
        self.timeout = timeout
        # Built lazily so importing this module never opens a session, and so
        # a test can inject a plain callable in its place.
        self._request_fn = request_fn
        self._provider_factory = provider_factory
        self._provider = None
        self._provider_credential = None

    @property
    def request_fn(self):
        if self._request_fn is None:
            self._request_fn = GoogleTransport(EgressConfig(timeout_seconds=self.timeout))
        return self._request_fn

    @request_fn.setter
    def request_fn(self, value):
        self._request_fn = value

    def _token_provider(self, credential):
        """One provider per credential, so a run mints one token, not four."""
        if self._provider is not None and self._provider_credential == credential:
            return self._provider
        provider = self._provider_factory(
            kind=self.site.gsc.auth_mode,
            secret=credential,
            session=getattr(self.request_fn, "session", None),
        )
        self._provider = provider
        self._provider_credential = credential
        return provider

    def fetch(self, **kwargs):
        return self._fetch(dimensions=("page", "query", "date"), **kwargs)

    def fetch_url_metrics(self, **kwargs):
        return self._fetch(dimensions=("page", "date"), **kwargs)

    def _fetch(self, *, dimensions, site_id, property_url, credential, start_date, end_date):
        client = GSCClient(
            GSCClientConfig(
                timeout_seconds=self.timeout,
                page_size=self.site.gsc.row_limit,
            ),
            request_fn=self.request_fn,
            token_provider=self._token_provider(credential),
        )
        response = client.query(
            site_id=site_id,
            site_url=property_url,
            start_date=start_date,
            end_date=end_date,
            dimensions=dimensions,
        )
        # GSC metrics are JSON numbers; preserve only exact non-negative counts.
        rows = []
        for original in response.raw_payload.get("rows", []):
            row = dict(original)
            for name in ("clicks", "impressions"):
                value = row.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value < 0
                    or int(value) != value
                ):
                    raise ValueError("Invalid GSC count.")
                row[name] = int(value)
            if len(row.get("keys", [])) != len(dimensions):
                raise ValueError("GSC response dimensions do not match request.")
            rows.append(row)
        return response.model_copy(
            update={"raw_payload": {**response.raw_payload, "rows": rows}}
        )
