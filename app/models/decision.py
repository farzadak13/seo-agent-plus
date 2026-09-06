from pydantic import BaseModel, ConfigDict

from app.models.actions import Action
from app.models.candidates import Candidate
from app.models.classification import ClassificationResult
from app.models.features import FeatureSet
from app.models.opportunities import Opportunity
from app.models.signals import Signal
from app.models.strategies import Strategy


class DecisionEngineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    features: FeatureSet
    signals: list[Signal]
    classification: ClassificationResult

    candidate: Candidate | None
    opportunity: Opportunity | None = None
    strategy: Strategy | None = None
    action: Action | None = None

    investigation_queue: list[Candidate]