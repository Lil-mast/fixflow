from __future__ import annotations

import json

from functions.review_report.app import handler as review_report
from shared.clock import epoch_seconds
from shared.repository import ReportRepository, WorkflowTokenRepository
from tests.support import (
    EVENTS_TABLE,
    REPORTS_TABLE,
    TOKENS_TABLE,
    FakeContext,
    FakeWorkflow,
    admin_api_event,
    make_settings,
    seed_report,
)


def dependencies():
    settings = make_settings()
    repository = ReportRepository(REPORTS_TABLE, EVENTS_TABLE, tokens_table=TOKENS_TABLE)
    tokens = WorkflowTokenRepository(TOKENS_TABLE)
    workflow = FakeWorkflow()
    return settings, repository, tokens, workflow


def pending_review(repository, tokens, *, version=2):
    report = seed_report(repository, status="REVIEW_PENDING", version=version)
    tokens.store_token(
        report_id=report["reportId"],
        task_type="REVIEW",
        task_token="opaque-review-token",
        execution_arn="arn:aws:states:us-east-1:1:execution:x:1",
        expires_at=epoch_seconds() + 3600,
    )
    return report


def decide(
    repository,
    tokens,
    workflow,
    report_id: str,
    body: dict,
    *,
    groups=("admin",),
):
    return review_report(
        admin_api_event(body, path_parameters={"reportId": report_id}, groups=groups),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        tokens=tokens,
        workflow=workflow,
    )


def test_approve_transitions_consumes_token_and_resumes_workflow(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)
    report_id = report["reportId"]

    response = decide(
        repository,
        tokens,
        workflow,
        report_id,
        {
            "decision": "APPROVE",
            "comment": "Verified the location and severity.",
            "publicMessage": "Report reviewed and approved for action.",
            "expectedVersion": 2,
        },
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "APPROVED"
    assert body["version"] == 3

    updated = repository.get_report(report_id)
    assert updated is not None
    assert updated["approvedAt"]
    assert updated["reviewedAt"]
    assert updated["publicSummary"] == "Report reviewed and approved for action."
    assert tokens.get_pending_token(report_id, "REVIEW") is None
    assert workflow.successes == [("opaque-review-token", {"decision": "APPROVE"})]

    events, _ = repository.list_public_events(report_id)
    assert events[-1]["action"] == "REVIEW_APPROVED"
    assert events[-1]["publicMessage"] == "Report reviewed and approved for action."


def test_missing_jwt_claims_are_unauthorized(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    from tests.support import api_event

    response = review_report(
        api_event(
            {"decision": "APPROVE", "expectedVersion": 2},
            path_parameters={"reportId": report["reportId"]},
        ),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        tokens=tokens,
        workflow=workflow,
    )

    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"]["code"] == "UNAUTHORIZED"


def test_non_admin_group_is_forbidden(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 2},
        groups=("resident",),
    )

    assert response["statusCode"] == 403
    assert json.loads(response["body"])["error"]["code"] == "FORBIDDEN"
    assert workflow.successes == []


def test_comma_separated_groups_are_accepted(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 2},
        groups="beta-testers, admin",
    )

    assert response["statusCode"] == 200


def test_decision_from_wrong_status_is_rejected(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = seed_report(repository, status="SUBMITTED", version=1)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 1},
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_stale_expected_version_is_a_conflict(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 1},
    )

    assert response["statusCode"] == 409
    error = json.loads(response["body"])["error"]
    assert error["code"] == "CONFLICT"
    assert error["details"] == {"expectedVersion": 1, "currentVersion": 2}


def test_missing_review_token_is_review_expired(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = seed_report(repository, status="REVIEW_PENDING", version=2)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 2},
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "REVIEW_EXPIRED"
    assert repository.get_report(report["reportId"])["status"] == "REVIEW_PENDING"


def test_review_uses_the_latest_replacement_token(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)
    tokens.store_token(
        report_id=report["reportId"],
        task_type="REVIEW",
        task_token="newer-token",
        execution_arn="arn:aws:states:us-east-1:1:execution:x:2",
        expires_at=epoch_seconds() + 3600,
    )

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "APPROVE", "expectedVersion": 2},
    )

    assert response["statusCode"] == 200
    assert workflow.successes == [("newer-token", {"decision": "APPROVE"})]
    assert repository.get_report(report["reportId"])["status"] == "APPROVED"


def test_reject_from_needs_info_has_no_token_and_closes_report(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = seed_report(repository, status="NEEDS_INFO", version=4)
    dynamodb.update_item(
        TableName=REPORTS_TABLE,
        Key={"reportId": {"S": report["reportId"]}},
        UpdateExpression="SET requiredActor = :actor",
        ExpressionAttributeValues={":actor": {"S": "RESIDENT"}},
    )

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {
            "decision": "REJECT",
            "comment": "No clarification after seven days.",
            "expectedVersion": 4,
        },
    )

    assert response["statusCode"] == 200
    updated = repository.get_report(report["reportId"])
    assert updated["status"] == "REJECTED"
    assert int(updated["version"]) == 5
    assert "requiredActor" not in updated
    assert workflow.successes == []


def test_repeat_info_request_stays_needs_info_and_appends_event(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = seed_report(repository, status="NEEDS_INFO", version=4)
    report_id = report["reportId"]
    before = len(repository.list_public_events(report_id)[0])

    response = decide(
        repository,
        tokens,
        workflow,
        report_id,
        {
            "decision": "REQUEST_INFO",
            "comment": "Please resend the nearest landmark.",
            "expectedVersion": 4,
        },
    )

    assert response["statusCode"] == 200
    updated = repository.get_report(report_id)
    assert updated["status"] == "NEEDS_INFO"
    assert int(updated["version"]) == 5
    assert updated["requiredActor"] == "RESIDENT"
    assert len(repository.list_public_events(report_id)[0]) == before + 1


def test_reject_and_request_info_require_a_comment(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    for decision in ("REJECT", "REQUEST_INFO"):
        response = decide(
            repository,
            tokens,
            workflow,
            report["reportId"],
            {"decision": decision, "expectedVersion": 2},
        )
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_decision_is_rejected(dynamodb) -> None:
    _, repository, tokens, workflow = dependencies()
    report = pending_review(repository, tokens)

    response = decide(
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "DEFER", "expectedVersion": 2},
    )

    assert response["statusCode"] == 400
