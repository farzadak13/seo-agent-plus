"""Three candidates: the proposal and two alternatives the customer can choose instead."""
from app.action.approval import approve, propose_title_change
from app.action.planner import build_action
from app.api.schemas import ActionResponse
from app.reasoning.prompt import CANDIDATE_COUNT, PROMPT_VERSION, build_title_reasoning_prompt
from tests.unit.test_stage37_38_actions import NEW_TITLE, OLD_TITLE, make_strategy


def pending(alternatives):
    return propose_title_change(
        action=build_action(strategy=make_strategy(), action_id="candidate-1"),
        run_id="run-1", tenant_id="principal-1", proposal_id="proposal:run-1",
        proposed_title=NEW_TITLE, current_title=OLD_TITLE, alternatives=alternatives,
    )


def test_the_alternatives_are_the_other_candidates_cleaned_and_distinct():
    managed = pending([NEW_TITLE, "  کفش مردانه   چرم ", "کفش مردانه چرم", "", "کفش اسپرت مردانه"])
    assert managed.action.parameters["alternative_titles"] == ["کفش مردانه چرم", "کفش اسپرت مردانه"]
    assert ActionResponse.from_managed(managed).alternative_titles == ["کفش مردانه چرم", "کفش اسپرت مردانه"]


def test_choosing_an_alternative_is_a_choice_not_an_edit():
    approved = approve(pending(["کفش مردانه چرم"]), actor="principal-1", title="کفش مردانه چرم")
    parameters = approved.action.parameters
    assert parameters["recommended_title"] == parameters["expected_title"] == "کفش مردانه چرم"
    assert parameters["chosen_alternative"] is True and parameters["edited_by_reviewer"] is False
    assert approved.history[-1].detail["chosen_alternative"] == "کفش مردانه چرم"


def test_a_title_of_ones_own_is_still_an_edit():
    approved = approve(pending(["کفش مردانه چرم"]), actor="principal-1", title="عنوان خودم")
    assert approved.action.parameters["edited_by_reviewer"] is True
    assert approved.history[-1].detail["edited_title"] == "عنوان خودم"


def test_a_proposal_without_alternatives_has_an_empty_list():
    assert ActionResponse.from_managed(pending(None)).alternative_titles == []


def test_the_model_is_asked_for_three_distinct_candidates():
    from app.models.reasoning import TitleReasoningInput

    # Only the fields the prompt reads; validation is the input model's own test.
    reasoning_input = TitleReasoningInput.model_construct(**{
        **{name: None for name in TitleReasoningInput.model_fields},
        "primary_query": "کفش", "competitor_titles": [], "constraints": [],
    })
    messages = build_title_reasoning_prompt(reasoning_input)
    assert CANDIDATE_COUNT == 3 and PROMPT_VERSION == "title-reasoning-v2"
    assert "exactly 3 candidates" in messages[1].content
