from datetime import date, datetime, timezone

from app.models.pipeline import PipelineStatus
from app.models.runs import SEORun, SEORunStatus
from app.models.sites import Site
from app.models.strategies import Strategy
from app.runs.handler import build_seo_run_handler


class FakeStore:
    def __init__(self, run):
        self.run = run

    def get(self, run_id):
        return self.run

    def update(self, run):
        self.run = run
        return run


class FakeSiteStore:
    def __init__(self, site):
        self.site = site

    def get(self, site_id):
        return self.site


class FakeRunService:
    def __init__(self, strategy):
        self.strategy = strategy
        self.calls = 0

    def run(self, **kwargs):
        self.calls += 1

        strategy = self.strategy

        class Result:
            status = PipelineStatus.COMPLETED
            
            def __init__(self):
                self.strategy = strategy

            def model_dump(self, mode="json"):
                return {"status": self.status.value, "strategy_id": self.strategy.strategy_id}

        return Result()


class FakeTitleWorkflow:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)

        class Proposal:
            proposal_id = "proposal-1"
            selected_title = "عنوان پیشنهادی"
            class Status:
                value = "completed"
            status = Status()

        return Proposal()


def make_site():
    return Site(
        site_id="site-1",
        principal_id="principal-1",
        name="Test",
        base_url="https://example.com/",
    )


def make_run():
    return SEORun(
        run_id="run-1",
        site_id="site-1",
        principal_id="principal-1",
        job_id="job-1",
        start_date=date(2026, 9, 1).isoformat(),
        end_date=date(2026, 9, 2).isoformat(),
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        candidate_id="candidate-1",
    )


def make_strategy():
    from app.models.opportunities import OpportunityType
    from app.models.strategies import StrategyStatus, StrategyType
    from app.models.snapshots import SnapshotMetadata

    return Strategy(
        strategy_id="strategy-1",
        opportunity_id="opportunity-1",
        site_id="site-1",
        normalized_url="https://example.com/page",
        normalized_query="کفش مردانه",
        opportunity_type=OpportunityType.VISIBILITY_GROWTH,
        strategy_type=StrategyType.SERP_TITLE_OPTIMIZATION,
        status=StrategyStatus.RECOMMENDED,
        confidence_score=1.0,
        expected_impact_score=0.8,
        effort_score=0.2,
        risk_score=0.1,
        priority_score=0.9,
        reasons=["test"],
        evidence={},
        snapshot=SnapshotMetadata(
            snapshot_id="snapshot-1",
            data_snapshot_id="data-1",
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=datetime.now(timezone.utc),
        ),
    )


def test_run_handler_attaches_title_proposal_to_run_result():
    run = make_run()
    strategy = make_strategy()
    run_store = FakeStore(run)
    title_workflow = FakeTitleWorkflow()
    handler = build_seo_run_handler(
        site_store=FakeSiteStore(make_site()),
        run_store=run_store,
        run_service=FakeRunService(strategy),
        title_workflow=title_workflow,
    )

    result = handler({"run_id": "run-1"})

    assert result["status"] == SEORunStatus.COMPLETED.value
    assert result["pipeline_status"] == PipelineStatus.COMPLETED.value
    assert result["title_proposal_id"] == "proposal-1"
    assert result["title_proposal_status"] == "completed"
    assert result["title_selected_title"] == "عنوان پیشنهادی"
    assert title_workflow.calls[0]["run_id"] == "run-1"
    assert run_store.run.status == SEORunStatus.COMPLETED
    assert run_store.run.result["title_proposal_id"] == "proposal-1"
