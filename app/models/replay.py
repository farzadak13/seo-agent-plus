from pydantic import BaseModel, ConfigDict

from app.models.actions import Action
from app.models.candidates import Candidate
from app.models.classification import ClassificationResult
from app.models.evidence import DecisionEvidence
from app.models.features import FeatureSet
from app.models.opportunities import Opportunity
from app.models.pipeline import PipelineStatus
from app.models.signals import Signal
from app.models.strategies import Strategy


# Backward-compatible alias.
# Replay consumes the same canonical evidence contract as production.
ReplayEvidence = DecisionEvidence


class ReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PipelineStatus

    features: FeatureSet | None
    signals: list[Signal]
    classification: ClassificationResult

    candidate: Candidate | None
    opportunity: Opportunity | None = None
    strategy: Strategy | None = None
    action: Action | None = None