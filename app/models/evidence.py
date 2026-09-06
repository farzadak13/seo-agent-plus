from datetime import date as Date

from pydantic import BaseModel, ConfigDict

from app.models.observations import DailyObservation, DataStatus
from app.models.reconciliation import ReconciliationResult
from app.models.snapshots import SnapshotMetadata
from app.models.url_metrics import URLDailyMetric


class DecisionEvidence(BaseModel):
    """
    Canonical, replayable evidence contract consumed by the decision engine.

    This model contains only persisted/derived evidence required to reproduce
    a decision. It contains no LLM output and no side effects.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str
    normalized_url: str
    normalized_query: str

    baseline_observations: list[DailyObservation]
    current_observations: list[DailyObservation]

    baseline_url_metrics: list[URLDailyMetric]
    current_url_metrics: list[URLDailyMetric]

    baseline_daily_status: list[tuple[Date, DataStatus]]
    current_daily_status: list[tuple[Date, DataStatus]]

    reconciliation: ReconciliationResult

    snapshot: SnapshotMetadata