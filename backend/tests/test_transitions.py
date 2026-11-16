from __future__ import annotations

import pytest

from shared.clock import epoch_seconds, now_iso
from shared.ids import new_ulid
from shared.repository import (
    InvalidStatus,
    ReportRepository,
    ReviewTokenUnavailable,
    VersionConflict,
    WorkflowTokenRepository,
)
from tests.support import EVENTS_TABLE, REPORTS_TABLE, TOKENS_TABLE, seed_report


def make_repository() -> ReportRepository:
    return ReportRepository(REPORTS_TABLE, EVENTS_TABLE, tokens_table=TOKENS_TABLE)


def make_event(
    report_id: str,
    *,
    previous: str,
    new: str,
    action: str = "TEST_ACTION",
    visibility: str = "ADMIN",
    public_message: str | None = None,
) -> dict:
    event = {
        "reportId": report_id,
        "eventId": new_ulid(),
        "actorType": "SYSTEM",
        "actorId": "SYSTEM",
        "action": action,
        "previousStatus": previous,
        "newStatus": new,
        "timestamp": now_iso(),
        "visibility": visibility,
    }
    if public_message:
        event["publicMessage"] = public_message
    return event


def event_count(dynamodb, report_id: str) -> int:
    response = dynamodb.query(
        TableName=EVENTS_TABLE,
        KeyConditionExpression="reportId = :reportId",
        ExpressionAttributeValues={":reportId": {"S": report_id}},
    )
    return len(response.get("Items", []))


def test_transition_updates_status_version_and_index(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository)
    report_id = report["reportId"]

    updated = repository.transition_report(
        report_id=report_id,
        expected_statuses=("SUBMITTED",),
        target_status="VALIDATING",
        fields={"workflowExecutionArn": "arn:aws:states:us-east-1:1:execution:x:1"},
        event=make_event(report_id, previous="SUBMITTED", new="VALIDATING"),
    )

    assert updated["status"] == "VALIDATING"
    assert int(updated["version"]) == 2
    assert updated["GSI1PK"] == "STATUS#VALIDATING"
    assert updated["workflowExecutionArn"].endswith(":1")
    assert event_count(dynamodb, report_id) == 2


def test_repeating_a_transition_is_a_successful_no_op(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository)
    report_id = report["reportId"]
    event = make_event(report_id, previous="SUBMITTED", new="VALIDATING")

    repository.transition_report(
        report_id=report_id,
        expected_statuses=("SUBMITTED",),
        target_status="VALIDATING",
        fields={},
        event=event,
    )
    again = repository.transition_report(
        report_id=report_id,
        expected_statuses=("SUBMITTED",),
        target_status="VALIDATING",
        fields={},
        event=make_event(report_id, previous="SUBMITTED", new="VALIDATING"),
    )

    assert again["status"] == "VALIDATING"
    assert int(again["version"]) == 2
    assert event_count(dynamodb, report_id) == 2


def test_illegal_source_status_is_rejected(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository, status="VALIDATING", version=2)

    with pytest.raises(InvalidStatus):
        repository.transition_report(
            report_id=report["reportId"],
            expected_statuses=("SUBMITTED",),
            target_status="REVIEW_PENDING",
            fields={},
            event=make_event(report["reportId"], previous="VALIDATING", new="REVIEW_PENDING"),
        )


def test_stale_expected_version_is_rejected(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository)

    with pytest.raises(VersionConflict):
        repository.transition_report(
            report_id=report["reportId"],
            expected_statuses=("SUBMITTED",),
            target_status="VALIDATING",
            fields={},
            event=make_event(report["reportId"], previous="SUBMITTED", new="VALIDATING"),
            expected_version=99,
        )


def test_same_status_transition_can_append_an_event(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository, status="NEEDS_INFO", version=4)

    updated = repository.transition_report(
        report_id=report["reportId"],
        expected_statuses=("NEEDS_INFO",),
        target_status="NEEDS_INFO",
        fields={"reasonCode": "INFO_REQUIRED", "requiredActor": "RESIDENT"},
        event=make_event(
            report["reportId"],
            previous="NEEDS_INFO",
            new="NEEDS_INFO",
            action="INFO_REQUESTED",
            visibility="PUBLIC",
            public_message="More information is needed from the resident.",
        ),
        allow_same_status=True,
    )

    assert updated["status"] == "NEEDS_INFO"
    assert int(updated["version"]) == 5
    assert updated["publicSummary"] == ("More information is needed from the resident.")
    assert event_count(dynamodb, report["reportId"]) == 2


def test_public_event_sets_public_summary_in_the_same_transaction(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository, status="REVIEW_PENDING", version=2)

    updated = repository.transition_report(
        report_id=report["reportId"],
        expected_statuses=("REVIEW_PENDING",),
        target_status="APPROVED",
        fields={},
        event=make_event(
            report["reportId"],
            previous="REVIEW_PENDING",
            new="APPROVED",
            action="REVIEW_APPROVED",
            visibility="PUBLIC",
            public_message="Report reviewed and approved for action.",
        ),
    )

    assert updated["publicSummary"] == "Report reviewed and approved for action."


def test_transition_consumes_the_pending_token(dynamodb) -> None:
    repository = make_repository()
    tokens = WorkflowTokenRepository(TOKENS_TABLE)
    report = seed_report(repository, status="REVIEW_PENDING", version=2)
    report_id = report["reportId"]

    stored = tokens.store_token(
        report_id=report_id,
        task_type="REVIEW",
        task_token="opaque-token",
        execution_arn="arn:aws:states:us-east-1:1:execution:x:1",
        expires_at=epoch_seconds() + 3600,
    )
    assert int(stored["taskVersion"]) == 1

    repository.transition_report(
        report_id=report_id,
        expected_statuses=("REVIEW_PENDING",),
        target_status="APPROVED",
        fields={},
        event=make_event(
            report_id,
            previous="REVIEW_PENDING",
            new="APPROVED",
            action="REVIEW_APPROVED",
            visibility="PUBLIC",
            public_message="Approved.",
        ),
        consume_token=("REVIEW", 1),
        consumed_by="admin-sub",
    )

    assert tokens.get_pending_token(report_id, "REVIEW") is None
    response = dynamodb.get_item(
        TableName=TOKENS_TABLE,
        Key={"reportId": {"S": report_id}, "taskType": {"S": "REVIEW"}},
    )
    token = response["Item"]
    assert token["status"]["S"] == "CONSUMED"
    assert token["consumedBy"]["S"] == "admin-sub"


def test_stale_token_rolls_back_the_report_update(dynamodb) -> None:
    repository = make_repository()
    tokens = WorkflowTokenRepository(TOKENS_TABLE)
    report = seed_report(repository, status="REVIEW_PENDING", version=2)
    report_id = report["reportId"]

    tokens.store_token(
        report_id=report_id,
        task_type="REVIEW",
        task_token="first",
        execution_arn="arn:execution:1",
        expires_at=epoch_seconds() + 3600,
    )
    tokens.store_token(
        report_id=report_id,
        task_type="REVIEW",
        task_token="second",
        execution_arn="arn:execution:2",
        expires_at=epoch_seconds() + 3600,
    )

    with pytest.raises(ReviewTokenUnavailable):
        repository.transition_report(
            report_id=report_id,
            expected_statuses=("REVIEW_PENDING",),
            target_status="APPROVED",
            fields={},
            event=make_event(
                report_id,
                previous="REVIEW_PENDING",
                new="APPROVED",
                action="REVIEW_APPROVED",
                visibility="PUBLIC",
                public_message="Approved.",
            ),
            consume_token=("REVIEW", 1),
        )

    unchanged = repository.get_report(report_id)
    assert unchanged is not None
    assert unchanged["status"] == "REVIEW_PENDING"
    assert int(unchanged["version"]) == 2


def test_mark_workflow_started_sets_operational_state(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository)

    repository.mark_workflow_started(report["reportId"], "arn:aws:states:us-east-1:1:execution:x:1")

    updated = repository.get_report(report["reportId"])
    assert updated is not None
    assert updated["workflowStatus"] == "STARTED"
    assert updated["workflowExecutionArn"].endswith(":1")


def test_workflow_failure_does_not_change_lifecycle_status(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository, status="REVIEW_PENDING", version=2)

    repository.record_workflow_failure(
        report_id=report["reportId"],
        event=make_event(
            report["reportId"],
            previous="REVIEW_PENDING",
            new="REVIEW_PENDING",
            action="WORKFLOW_FAILED",
        ),
        execution_arn="arn:aws:states:us-east-1:1:execution:x:1",
    )

    updated = repository.get_report(report["reportId"])
    assert updated is not None
    assert updated["workflowStatus"] == "FAILED"
    assert updated["status"] == "REVIEW_PENDING"
    assert event_count(dynamodb, report["reportId"]) == 2


def test_priority_step_is_idempotent(dynamodb) -> None:
    repository = make_repository()
    report = seed_report(repository, status="VALIDATING", version=2)
    report_id = report["reportId"]
    event = make_event(
        report_id, previous="VALIDATING", new="VALIDATING", action="PRIORITY_CALCULATED"
    )

    repository.set_priority(report_id=report_id, score=50, priority="P2_HIGH", event=event)
    count = event_count(dynamodb, report_id)
    repository.set_priority(
        report_id=report_id,
        score=50,
        priority="P2_HIGH",
        event=make_event(
            report_id,
            previous="VALIDATING",
            new="VALIDATING",
            action="PRIORITY_CALCULATED",
        ),
    )

    assert event_count(dynamodb, report_id) == count
    updated = repository.get_report(report_id)
    assert updated is not None
    assert updated["priority"] == "P2_HIGH"
