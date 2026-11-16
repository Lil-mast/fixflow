from __future__ import annotations

import pytest

from functions.calculate_priority.app import handler as calculate_priority
from functions.record_workflow_failure.app import handler as record_workflow_failure
from functions.store_task_token.app import handler as store_task_token
from functions.transition_report.app import handler as transition_report
from functions.validate_report.app import handler as validate_report
from shared.clock import epoch_seconds
from shared.repository import (
    ReportRepository,
    WorkflowTokenRepository,
)
from tests.support import (
    EVENTS_TABLE,
    REPORTS_TABLE,
    TOKENS_TABLE,
    make_settings,
    seed_report,
)


def repository() -> ReportRepository:
    return ReportRepository(REPORTS_TABLE, EVENTS_TABLE, tokens_table=TOKENS_TABLE)


def test_validate_accepts_a_stored_report(dynamodb) -> None:
    report = seed_report(repository())

    result = validate_report(
        {"reportId": report["reportId"]},
        settings=make_settings(),
        repository=repository(),
    )

    assert result == {
        "reportId": report["reportId"],
        "valid": True,
        "reasonCode": None,
    }


def test_validate_rejects_a_corrupt_record(dynamodb) -> None:
    report = seed_report(repository())
    dynamodb.update_item(
        TableName=REPORTS_TABLE,
        Key={"reportId": {"S": report["reportId"]}},
        UpdateExpression="SET description = :short",
        ExpressionAttributeValues={":short": {"S": "short"}},
    )

    result = validate_report(
        {"reportId": report["reportId"]},
        settings=make_settings(),
        repository=repository(),
    )

    assert result["valid"] is False
    assert result["reasonCode"] == "INVALID_DESCRIPTION"


def test_validate_missing_report_is_a_business_result(dynamodb) -> None:
    result = validate_report(
        {"reportId": "does-not-exist"},
        settings=make_settings(),
        repository=repository(),
    )

    assert result["valid"] is False
    assert result["reasonCode"] == "REPORT_NOT_FOUND"


def test_priority_handler_stores_the_published_score(dynamodb) -> None:
    report = seed_report(repository(), status="VALIDATING", version=2)

    result = calculate_priority(
        {"reportId": report["reportId"]},
        settings=make_settings(),
        repository=repository(),
    )

    assert result["priority"] == "P2_HIGH"
    assert result["priorityScore"] == 50
    stored = repository().get_report(report["reportId"])
    assert stored["priority"] == "P2_HIGH"
    assert int(stored["priorityScore"]) == 50


def test_transition_handler_applies_validation_start(dynamodb) -> None:
    report = seed_report(repository())

    result = transition_report(
        {
            "reportId": report["reportId"],
            "targetStatus": "VALIDATING",
            "expectedStatuses": ["SUBMITTED"],
            "action": "VALIDATION_STARTED",
            "visibility": "ADMIN",
            "executionArn": "arn:aws:states:us-east-1:1:execution:x:1",
        },
        settings=make_settings(),
        repository=repository(),
    )

    assert result["status"] == "VALIDATING"
    assert result["version"] == 2
    stored = repository().get_report(report["reportId"])
    assert stored["workflowExecutionArn"].endswith(":1")


def test_transition_handler_sets_public_summary_for_a_public_event(dynamodb) -> None:
    report = seed_report(repository(), status="REVIEW_PENDING", version=2)

    transition_report(
        {
            "reportId": report["reportId"],
            "targetStatus": "APPROVED",
            "expectedStatuses": ["REVIEW_PENDING"],
            "action": "REVIEW_APPROVED",
            "visibility": "PUBLIC",
            "publicMessage": "Report reviewed and approved for action.",
        },
        settings=make_settings(),
        repository=repository(),
    )

    stored = repository().get_report(report["reportId"])
    assert stored["publicSummary"] == "Report reviewed and approved for action."
    events, _ = repository().list_public_events(report["reportId"])
    assert events[-1]["action"] == "REVIEW_APPROVED"


def test_transition_handler_ignores_an_already_decided_report(dynamodb) -> None:
    report = seed_report(repository(), status="APPROVED", version=3)

    result = transition_report(
        {
            "reportId": report["reportId"],
            "targetStatus": "REVIEW_EXPIRED",
            "expectedStatuses": ["REVIEW_PENDING", "REVIEW_EXPIRED"],
            "action": "REVIEW_EXPIRED",
            "visibility": "ADMIN",
            "allowSameStatus": True,
            "ignoreIfStatus": ["APPROVED", "REJECTED", "NEEDS_INFO"],
        },
        settings=make_settings(),
        repository=repository(),
    )

    assert result["status"] == "APPROVED"
    assert int(result["version"]) == 3


def test_store_task_token_returns_version_without_returning_the_token(dynamodb) -> None:
    settings = make_settings()
    report_id = seed_report(repository())["reportId"]

    result = store_task_token(
        {
            "reportId": report_id,
            "taskType": "REVIEW",
            "taskToken": "opaque-token",
            "executionArn": "arn:aws:states:us-east-1:1:execution:x:1",
            "timeoutSeconds": 3600,
        },
        settings=settings,
    )

    assert result["taskVersion"] == 1
    assert "taskToken" not in result
    stored = WorkflowTokenRepository(TOKENS_TABLE).get_pending_token(report_id, "REVIEW")
    assert stored is not None
    assert stored["taskToken"] == "opaque-token"
    assert int(stored["expiresAt"]) > epoch_seconds() + 3600


def test_store_task_token_rejects_missing_fields(dynamodb) -> None:
    with pytest.raises(ValueError):
        store_task_token(
            {"reportId": "x", "taskType": "REVIEW"},
            settings=make_settings(),
        )


def test_workflow_failure_handler_keeps_lifecycle_status(dynamodb) -> None:
    report = seed_report(repository(), status="REVIEW_PENDING", version=2)

    result = record_workflow_failure(
        {
            "reportId": report["reportId"],
            "reason": "Lambda.TooManyRequestsException",
            "executionArn": "arn:aws:states:us-east-1:1:execution:x:1",
        },
        settings=make_settings(),
        repository=repository(),
    )

    assert result["workflowStatus"] == "FAILED"
    stored = repository().get_report(report["reportId"])
    assert stored["status"] == "REVIEW_PENDING"
    assert stored["workflowStatus"] == "FAILED"
