from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from app.models.observations import DailyObservation
from app.models.url_metrics import URLDailyMetric
from app.models.gsc import RawGSCResponse
from app.models.pipeline import DecisionPipelineResult


class GSCDataGateway(Protocol):
    def fetch(
        self,
        *,
        site_id: str,
        property_url: str,
        credential: str,
        start_date: date,
        end_date: date,
    ) -> RawGSCResponse:
        ...


class BaselineProvider(Protocol):
    def load(
        self,
        *,
        site_id: str,
        normalized_url: str,
        normalized_query: str,
        end_date: date,
    ) -> tuple[Sequence[DailyObservation], Sequence[URLDailyMetric]]:
        ...


class PipelineRunner(Protocol):
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
        ...
