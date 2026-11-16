from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

STATEMACHINE = Path(__file__).resolve().parents[1] / "statemachines" / "report_workflow.asl.json"

SUBSTITUTIONS = {
    "ValidateReportFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:validate",
    "CalculatePriorityFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:priority",
    "TransitionReportFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:transition",
    "StoreTaskTokenFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:token",
    "RecordWorkflowFailureFunctionArn": "arn:aws:lambda:us-east-1:123456789012:function:failure",
    "ReviewTimeoutSeconds": "259200",
    "LifecycleTimeoutSeconds": "1209600",
}


def load_definition() -> dict[str, Any]:
    raw = STATEMACHINE.read_text(encoding="utf-8")
    for key, value in SUBSTITUTIONS.items():
        raw = raw.replace(f"${{{key}}}", value)
    return json.loads(raw)


def test_definition_is_valid_json_after_substitution() -> None:
    definition = load_definition()
    assert definition["StartAt"] in definition["States"]


def test_every_transition_target_exists() -> None:
    definition = load_definition()
    states = definition["States"]
    targets: list[str] = []

    for state in states.values():
        if "Next" in state:
            targets.append(state["Next"])
        if "Default" in state:
            targets.append(state["Default"])
        for choice in state.get("Choices", []):
            targets.append(choice["Next"])
        for catcher in state.get("Catch", []):
            targets.append(catcher["Next"])

    missing = sorted(set(targets) - set(states))
    assert missing == []


def test_no_unresolved_placeholders_remain() -> None:
    definition = load_definition()
    text = json.dumps(definition)
    assert re.search(r"\$\{[A-Za-z0-9_]+\}", text) is None


def test_review_wait_has_a_timeout_and_timeout_catch() -> None:
    states = load_definition()["States"]
    token_state = states["StoreReviewToken"]

    assert token_state["Resource"].endswith("lambda:invoke.waitForTaskToken")
    assert token_state["TimeoutSeconds"] == 259200
    timeout_catches = [
        catcher for catcher in token_state["Catch"] if "States.Timeout" in catcher["ErrorEquals"]
    ]
    assert timeout_catches
    assert timeout_catches[0]["Next"] == "MarkReviewExpired"

    payload = token_state["Parameters"]["Payload"]
    assert payload["taskToken.$"] == "$$.Task.Token"
    assert payload["taskType"] == "REVIEW"


def test_review_choice_reads_the_callback_decision() -> None:
    states = load_definition()["States"]

    assert states["ReviewDecision"]["Choices"] == [
        {
            "Variable": "$.review.decision",
            "StringEquals": "APPROVE",
            "Next": "MarkApproved",
        },
        {
            "Variable": "$.review.decision",
            "StringEquals": "REJECT",
            "Next": "MarkRejected",
        },
        {
            "Variable": "$.review.decision",
            "StringEquals": "REQUEST_INFO",
            "Next": "MarkNeedsInfo",
        },
    ]
    assert states["MarkReviewExpired"]["Next"] == "StoreReviewToken"


def test_execution_input_does_not_carry_resident_content() -> None:
    definition = load_definition()
    text = json.dumps(definition)
    for forbidden in ("description", "evidenceKey", "latitude", "longitude"):
        assert forbidden not in text


def test_validation_failure_is_a_business_branch_not_a_task_failure() -> None:
    states = load_definition()["States"]
    assert states["ValidationResult"]["Type"] == "Choice"
    assert states["MarkValidationFailed"]["End"] is True


def test_approved_reports_enter_the_assignment_wait() -> None:
    states = load_definition()["States"]
    assert states["MarkApproved"]["Next"] == "StoreAssignmentToken"

    for state_name, task_type in (
        ("StoreAssignmentToken", "ASSIGNMENT"),
        ("StoreResolutionToken", "RESOLUTION"),
        ("StoreVerificationToken", "VERIFICATION"),
    ):
        state = states[state_name]
        assert state["Resource"].endswith("lambda:invoke.waitForTaskToken")
        assert state["TimeoutSeconds"] == 1209600
        assert state["Parameters"]["Payload"]["taskType"] == task_type
        timeout_catch = [
            catcher for catcher in state["Catch"] if "States.Timeout" in catcher["ErrorEquals"]
        ]
        assert timeout_catch
        assert timeout_catch[0]["Next"] == state_name


def test_verification_decision_closes_or_reopens_the_report() -> None:
    states = load_definition()["States"]
    assert states["VerificationDecision"]["Choices"] == [
        {
            "Variable": "$.verification.decision",
            "StringEquals": "VERIFIED",
            "Next": "MarkResolved",
        },
        {
            "Variable": "$.verification.decision",
            "StringEquals": "REJECTED",
            "Next": "MarkInProgressAfterRejection",
        },
    ]
    assert states["MarkResolved"]["End"] is True
    assert states["MarkInProgressAfterRejection"]["Next"] == "StoreResolutionToken"
