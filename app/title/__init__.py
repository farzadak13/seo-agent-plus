from app.title.engine import build_title_recommendation

__all__ = [
    "build_title_recommendation",
    "TitleProposalStore",
    "TitleRecommendationWorkflow",
    "TitleWorkflowError",
]


def __getattr__(name: str):
    if name in {
        "TitleProposalStore",
        "TitleRecommendationWorkflow",
        "TitleWorkflowError",
    }:
        from app.title.workflow import (
            TitleProposalStore,
            TitleRecommendationWorkflow,
            TitleWorkflowError,
        )
        return {
            "TitleProposalStore": TitleProposalStore,
            "TitleRecommendationWorkflow": TitleRecommendationWorkflow,
            "TitleWorkflowError": TitleWorkflowError,
        }[name]
    raise AttributeError(name)
