from datetime import date
from types import SimpleNamespace
import pytest

from app.gsc.client import GSCClient, GSCClientError
from app.gsc.config import GSCClientConfig

class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
    def json(self):
        return self._payload

def make_client(request_fn, retries=2):
    return GSCClient(
        GSCClientConfig(oauth_access_token="token", max_retries=retries, page_size=2),
        request_fn=request_fn,
        sleep_fn=lambda _: None,
    )

def test_auth_modes():
    assert GSCClientConfig().auth_mode() == "none"
    assert GSCClientConfig(oauth_access_token="x").auth_mode() == "access_token"
    assert GSCClientConfig(service_account_json="{}").auth_mode() == "service_account"

def test_invalid_date_range():
    client = make_client(lambda *a, **k: None)
    with pytest.raises(ValueError):
        client.query(site_id="s", site_url="https://example.com/", start_date=date(2026,9,8), end_date=date(2026,9,7))

def test_returns_raw_gsc_response():
    calls = []
    def request_fn(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return FakeResponse(200, {"rows":[{"keys":["u","q","2026-09-07"],"clicks":10,"impressions":100}]})
    result = make_client(request_fn).query(
        site_id="site-1", site_url="https://example.com/",
        start_date=date(2026,9,7), end_date=date(2026,9,7)
    )
    assert result.site_id == "site-1"
    assert result.raw_payload["rows"][0]["clicks"] == 10
    assert result.response_id.startswith("gsc-")
    assert calls[0][2]["headers"]["Authorization"] == "Bearer token"

def test_paginates():
    starts = []
    def request_fn(method, path, **kwargs):
        starts.append(kwargs["json"]["startRow"])
        if kwargs["json"]["startRow"] == 0:
            rows = [{"keys":[str(i)],"clicks":i,"impressions":i+1} for i in range(2)]
        else:
            rows = [{"keys":["last"],"clicks":3,"impressions":4}]
        return FakeResponse(200, {"rows": rows})
    result = make_client(request_fn).query(
        site_id="s", site_url="https://example.com/",
        start_date=date(2026,9,7), end_date=date(2026,9,7)
    )
    assert starts == [0,2]
    assert len(result.raw_payload["rows"]) == 3

def test_retries_transient():
    count = {"v":0}
    def request_fn(*a, **k):
        count["v"] += 1
        return FakeResponse(503, {}) if count["v"] < 3 else FakeResponse(200, {"rows":[]})
    result = make_client(request_fn, retries=2).query(
        site_id="s", site_url="https://example.com/",
        start_date=date(2026,9,7), end_date=date(2026,9,7)
    )
    assert result.raw_payload["rows"] == []
    assert count["v"] == 3

def test_does_not_retry_auth_error():
    count = {"v":0}
    def request_fn(*a, **k):
        count["v"] += 1
        return FakeResponse(401, {})
    with pytest.raises(GSCClientError) as exc:
        make_client(request_fn, retries=3).query(
            site_id="s", site_url="https://example.com/",
            start_date=date(2026,9,7), end_date=date(2026,9,7)
        )
    assert exc.value.status_code == 401
    assert count["v"] == 1

def test_rejects_non_object_json():
    def request_fn(*a, **k):
        return SimpleNamespace(status_code=200, json=lambda: ["bad"])
    with pytest.raises(GSCClientError, match="must be an object"):
        make_client(request_fn).query(
            site_id="s", site_url="https://example.com/",
            start_date=date(2026,9,7), end_date=date(2026,9,7)
        )

def test_response_id_is_deterministic():
    def request_fn(*a, **k):
        return FakeResponse(200, {"rows":[]})
    client = make_client(request_fn)
    kwargs = dict(site_id="s", site_url="https://example.com/", start_date=date(2026,9,7), end_date=date(2026,9,7))
    assert client.query(**kwargs).response_id == client.query(**kwargs).response_id

def test_flows_into_existing_ingestion():
    from app.ingestion.gsc import ingest_gsc_response
    def request_fn(*a, **k):
        return FakeResponse(200, {"rows":[{
            "keys":["https://example.com/page","کفش مردانه","2026-09-07"],
            "clicks":10,"impressions":100,"position":5.0
        }]})
    raw = make_client(request_fn).query(
        site_id="site-1", site_url="https://example.com/",
        start_date=date(2026,9,7), end_date=date(2026,9,7)
    )
    result = ingest_gsc_response(
        response=raw, start_date=date(2026,9,7), end_date=date(2026,9,7)
    )
    assert len(result.observations) == 1
    assert result.observations[0].clicks == 10
