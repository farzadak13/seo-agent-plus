from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from app.models.gsc import RawGSCResponse
from app.models.observations import DailyObservation
from app.models.pipeline import DecisionPipelineResult
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric
from app.pipeline.decision import run_decision_pipeline
from app.runs.contracts import PipelineRunner


class DeterministicPipelineRunner(PipelineRunner):
    """Adapter from the application run contract to the existing pipeline."""

    def run(
        self,
        *,
        response: RawGSCResponse,
        start_date: date,
        end_date: date,
        baseline_observations: Sequence[DailyObservation],
        baseline_url_metrics: Sequence[URLDailyMetric],
        candidate_id: str,
        normalized_url: str,
        normalized_query: str,
    ) -> DecisionPipelineResult:
        snapshot = SnapshotMetadata(
            snapshot_id=f"run:{response.response_id}",
            data_snapshot_id=response.response_id,
            rule_version="rules-v1",
            config_version="config-v1",
            generated_at=response.created_at,
        )
        return run_decision_pipeline(
            response=response,
            start_date=start_date,
            end_date=end_date,
            baseline_observations=baseline_observations,
            baseline_url_metrics=baseline_url_metrics,
            snapshot=snapshot,
            candidate_id=candidate_id,
            normalized_url=normalized_url,
            normalized_query=normalized_query,
        )
