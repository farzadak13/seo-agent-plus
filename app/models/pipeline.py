from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.actions import Action
from app.models.candidates import Candidate
from app.models.classification import ClassificationResult
from app.models.evidence import DecisionEvidence
from app.models.features import FeatureSet
from app.models.gsc import RawGSCResponse
from app.models.ingestion import GSCIngestionResult
from app.models.opportunities import Opportunity
from app.models.reconciliation import ReconciliationResult
from app.models.signals import Signal
from app.models.strategies import Strategy


class PipelineStatus(StrEnum):
    COMPLETED = "completed"
    REJECTED_RECONCILIATION = "rejected_reconciliation"
    REJECTED_DATA_QUALITY = "rejected_data_quality"


class DecisionPipelineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PipelineStatus

    ingestion: GSCIngestionResult
    reconciliation: ReconciliationResult

    evidence: DecisionEvidence | None

    features: FeatureSet | None
    signals: list[Signal]

    classification: ClassificationResult

    candidate: Candidate | None
    opportunity: Opportunity | None = None
    strategy: Strategy | None = None
    action: Action | None = None

    investigation_queue: list[Candidate]