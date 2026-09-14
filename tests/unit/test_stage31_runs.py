from datetime import date, datetime, timezone

from app.models.pipeline import PipelineStatus
from app.models.gsc import RawGSCResponse
from app.models.sites import GSCConnectionConfig, SecretRef, Site
from app.onboarding.secrets import EnvironmentSecretResolver
from app.onboarding.site_store import SiteStore
from app.persistence.memory import InMemoryRepository
from app.runs.service import SEORunService, RunServiceError


class FakeGateway:
    def __init__(self):
        self.calls = []

    def fetch(self, *, site_id, property_url, credential, start_date, end_date):
        self.calls.append((site_id, property_url, credential, start_date, end_date))
        return RawGSCResponse(
            response_id="gsc-response-001",
            site_id=site_id,
            fetch_date=end_date,
            raw_payload={"rows": []},
            created_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
        )


class FakeBaseline:
    def load(self, **kwargs):
        return [], []


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Result",
            (),
            {
                "status": PipelineStatus.COMPLETED,
            },
        )()


def make_site():
    return Site(
        site_id="site-001",
        principal_id="principal-1",
        name="Example",
        base_url="https://example.com",
        gsc=GSCConnectionConfig(
            property_url="https://example.com",
            credential_ref=SecretRef(key="GSC_TOKEN"),
        ),
    )


def test_run_service_resolves_secret_at_execution(monkeypatch):
    monkeypatch.setenv("GSC_TOKEN", "secret-token")
    gateway = FakeGateway()
    runner = FakeRunner()
    service = SEORunService(
        secret_resolver=EnvironmentSecretResolver(),
        gsc_gateway=gateway,
        baseline_provider=FakeBaseline(),
        pipeline_runner=runner,
    )
    result = service.run(
        site=make_site(),
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 12),
        normalized_url="https://example.com/page",
        normalized_query="کفش",
        candidate_id="candidate-1",
    )
    assert result.status == PipelineStatus.COMPLETED
    assert gateway.calls[0][2] == "secret-token"
    assert runner.calls[0]["candidate_id"] == "candidate-1"


def test_run_service_requires_gsc_configuration():
    site = make_site().model_copy(update={"gsc": None})
    service = SEORunService(
        secret_resolver=EnvironmentSecretResolver(),
        gsc_gateway=FakeGateway(),
        baseline_provider=FakeBaseline(),
        pipeline_runner=FakeRunner(),
    )
    try:
        service.run(
            site=site,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            normalized_url="https://example.com/page",
            normalized_query="کفش",
            candidate_id="candidate-1",
        )
    except RunServiceError as exc:
        assert "GSC" in str(exc)
    else:
        raise AssertionError("Expected missing GSC configuration to fail")


def test_run_service_does_not_receive_secret_from_persisted_site(monkeypatch):
    monkeypatch.setenv("GSC_TOKEN", "runtime-only")
    site = make_site()
    serialized = site.model_dump(mode="json")
    assert serialized["gsc"]["credential_ref"]["key"] == "GSC_TOKEN"
    assert "runtime-only" not in str(serialized)
