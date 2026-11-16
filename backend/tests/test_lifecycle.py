from __future__ import annotations

import json

import boto3

from functions.assign_report.app import handler as assign_report
from functions.create_resolution_upload_url.app import (
    handler as create_resolution_upload_url,
)
from functions.submit_resolution.app import handler as submit_resolution
from functions.verify_resolution.app import handler as verify_resolution
from shared.clock import epoch_seconds
from shared.repository import ReportRepository, WorkflowTokenRepository
from shared.storage import EvidenceStorage
from tests.support import (
    EVENTS_TABLE,
    EVIDENCE_BUCKET,
    REPORTS_TABLE,
    TOKENS_TABLE,
    FakeContext,
    FakeWorkflow,
    admin_api_event,
    make_settings,
    seed_report,
)


def dependencies():
    repository = ReportRepository(REPORTS_TABLE, EVENTS_TABLE, tokens_table=TOKENS_TABLE)
    tokens = WorkflowTokenRepository(TOKENS_TABLE)
    workflow = FakeWorkflow()
    return repository, tokens, workflow


def waiting_report(repository, tokens, *, status: str, version: int, task: str):
    report = seed_report(repository, status=status, version=version)
    tokens.store_token(
        report_id=report["reportId"],
        task_type=task,
        task_token=f"opaque-{task.lower()}-token",
        execution_arn="arn:aws:states:us-east-1:1:execution:x:1",
        expires_at=epoch_seconds() + 3600,
    )
    return report


def call(handler, repository, tokens, workflow, report_id, body, **kwargs):
    return handler(
        admin_api_event(body, path_parameters={"reportId": report_id}),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        tokens=tokens,
        workflow=workflow,
        **kwargs,
    )


def test_assign_moves_report_to_in_progress_and_resumes_workflow(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="APPROVED", version=4, task="ASSIGNMENT")
    report_id = report["reportId"]

    response = call(
        assign_report,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "assignedTeam": "Roads Team A",
            "comment": "Assigned to the crew covering Kariakoo.",
            "publicMessage": "Assigned to the roads team.",
            "expectedVersion": 4,
        },
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "IN_PROGRESS"
    assert body["assignedTeam"] == "Roads Team A"
    assert body["version"] == 5

    stored = repository.get_report(report_id)
    assert stored is not None
    assert stored["assignedTeam"] == "Roads Team A"
    assert stored["assignedAt"]
    assert stored["publicSummary"] == "Assigned to the roads team."
    assert tokens.get_pending_token(report_id, "ASSIGNMENT") is None
    assert workflow.successes == [("opaque-assignment-token", {"assignedTeam": "Roads Team A"})]


def test_assign_requires_an_approved_report(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(
        repository, tokens, status="REVIEW_PENDING", version=3, task="ASSIGNMENT"
    )

    response = call(
        assign_report,
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"assignedTeam": "Roads Team A", "expectedVersion": 3},
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "INVALID_STATE_TRANSITION"
    assert workflow.successes == []


def test_assign_without_a_pending_token_is_task_expired(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = seed_report(repository, status="APPROVED", version=4)

    response = call(
        assign_report,
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"assignedTeam": "Roads Team A", "expectedVersion": 4},
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "TASK_EXPIRED"
    assert repository.get_report(report["reportId"])["status"] == "APPROVED"


def test_assign_rejects_a_stale_version(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="APPROVED", version=4, task="ASSIGNMENT")

    response = call(
        assign_report,
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"assignedTeam": "Roads Team A", "expectedVersion": 3},
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "CONFLICT"


def test_resolution_upload_requires_in_progress_and_uses_the_resolution_prefix(
    dynamodb, s3
) -> None:
    repository, _, _ = dependencies()
    report = seed_report(repository, status="IN_PROGRESS", version=5)
    store = EvidenceStorage(EVIDENCE_BUCKET, client=s3)

    response = create_resolution_upload_url(
        admin_api_event(
            {"contentType": "image/jpeg", "contentLength": 1024},
            path_parameters={"reportId": report["reportId"]},
        ),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        storage=store,
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["objectKey"].startswith(f"incoming/resolution/{report['reportId']}/")
    assert body["upload"]["fields"]["x-amz-meta-purpose"] == "resolution"


def test_resolution_upload_is_rejected_before_assignment(dynamodb, s3) -> None:
    repository, _, _ = dependencies()
    report = seed_report(repository, status="APPROVED", version=4)

    response = create_resolution_upload_url(
        admin_api_event(
            {"contentType": "image/jpeg", "contentLength": 1024},
            path_parameters={"reportId": report["reportId"]},
        ),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        storage=EvidenceStorage(EVIDENCE_BUCKET, client=s3),
    )

    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_submit_resolution_copies_evidence_and_resumes_the_workflow(dynamodb, s3) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="IN_PROGRESS", version=5, task="RESOLUTION")
    report_id = report["reportId"]
    upload_id = "9a8b7c6d-5e4f-4a3b-8c1d-0e9f8a7b6c5d"
    object_key = f"incoming/resolution/{report_id}/{upload_id}.jpg"
    payload = b"resolution-photo"
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=object_key,
        Body=payload,
        ContentType="image/jpeg",
        Metadata={
            "report-id": report_id,
            "upload-id": upload_id,
            "purpose": "resolution",
        },
    )

    response = call(
        submit_resolution,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "evidence": {
                "objectKey": object_key,
                "uploadId": upload_id,
                "contentType": "image/jpeg",
                "contentLength": len(payload),
            },
            "note": "Pothole filled, compacted, and the lane reopened.",
            "publicMessage": "Repair completed and awaiting verification.",
            "expectedVersion": 5,
        },
        storage=EvidenceStorage(EVIDENCE_BUCKET, client=s3),
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "RESOLUTION_PENDING"
    assert body["version"] == 6

    stored = repository.get_report(report_id)
    assert stored is not None
    assert stored["resolutionEvidenceKey"] == f"resolution/{report_id}/{upload_id}.jpg"
    assert stored["resolutionContentType"] == "image/jpeg"
    assert stored["resolutionSize"] == len(payload)
    assert stored["resolutionSubmittedAt"]
    assert tokens.get_pending_token(report_id, "RESOLUTION") is None
    assert workflow.successes == [("opaque-resolution-token", {"submitted": True})]

    copied = s3.get_object(Bucket=EVIDENCE_BUCKET, Key=stored["resolutionEvidenceKey"])
    assert copied["Body"].read() == payload


def test_submit_resolution_rejects_mismatched_upload_metadata(dynamodb, s3) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="IN_PROGRESS", version=5, task="RESOLUTION")
    report_id = report["reportId"]
    upload_id = "9a8b7c6d-5e4f-4a3b-8c1d-0e9f8a7b6c5d"
    object_key = f"incoming/resolution/{report_id}/{upload_id}.jpg"
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=object_key,
        Body=b"x",
        ContentType="image/jpeg",
        Metadata={
            "report-id": report_id,
            "upload-id": upload_id,
            "purpose": "report",
        },
    )

    response = call(
        submit_resolution,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "evidence": {
                "objectKey": object_key,
                "uploadId": upload_id,
                "contentType": "image/jpeg",
                "contentLength": 1,
            },
            "note": "Done.",
            "expectedVersion": 5,
        },
        storage=EvidenceStorage(EVIDENCE_BUCKET, client=s3),
    )

    assert response["statusCode"] == 422
    assert repository.get_report(report_id)["status"] == "IN_PROGRESS"
    assert workflow.successes == []


def test_verify_resolution_marks_the_report_resolved(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(
        repository,
        tokens,
        status="RESOLUTION_PENDING",
        version=6,
        task="VERIFICATION",
    )
    report_id = report["reportId"]

    response = call(
        verify_resolution,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "decision": "VERIFIED",
            "comment": "Evidence matches the reported location.",
            "publicMessage": "Repair verified and report closed.",
            "expectedVersion": 6,
        },
    )

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "RESOLVED"
    assert body["resolvedAt"]

    stored = repository.get_report(report_id)
    assert stored["resolvedAt"]
    assert tokens.get_pending_token(report_id, "VERIFICATION") is None
    assert workflow.successes == [("opaque-verification-token", {"decision": "VERIFIED"})]


def test_rejected_resolution_returns_the_report_to_in_progress(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(
        repository,
        tokens,
        status="RESOLUTION_PENDING",
        version=6,
        task="VERIFICATION",
    )
    report_id = report["reportId"]

    response = call(
        verify_resolution,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "decision": "REJECTED",
            "comment": "The photo does not show the repaired section.",
            "expectedVersion": 6,
        },
    )

    assert response["statusCode"] == 200
    stored = repository.get_report(report_id)
    assert stored["status"] == "IN_PROGRESS"
    assert stored["resolutionRejectionReason"] == "The photo does not show the repaired section."
    assert workflow.successes == [("opaque-verification-token", {"decision": "REJECTED"})]


def test_rejection_requires_a_comment(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(
        repository,
        tokens,
        status="RESOLUTION_PENDING",
        version=6,
        task="VERIFICATION",
    )

    response = call(
        verify_resolution,
        repository,
        tokens,
        workflow,
        report["reportId"],
        {"decision": "REJECTED", "expectedVersion": 6},
    )

    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "VALIDATION_ERROR"


def test_lifecycle_callbacks_require_the_admin_group(dynamodb) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="APPROVED", version=4, task="ASSIGNMENT")

    response = assign_report(
        admin_api_event(
            {"assignedTeam": "Roads Team A", "expectedVersion": 4},
            path_parameters={"reportId": report["reportId"]},
            groups=("resident",),
        ),
        FakeContext(),
        settings=make_settings(),
        repository=repository,
        tokens=tokens,
        workflow=workflow,
    )

    assert response["statusCode"] == 403
    assert workflow.successes == []


def test_resolution_object_is_stored_in_the_private_prefix(dynamodb, s3) -> None:
    repository, tokens, workflow = dependencies()
    report = waiting_report(repository, tokens, status="IN_PROGRESS", version=5, task="RESOLUTION")
    report_id = report["reportId"]
    upload_id = "9a8b7c6d-5e4f-4a3b-8c1d-0e9f8a7b6c5d"
    object_key = f"incoming/resolution/{report_id}/{upload_id}.png"
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=object_key,
        Body=b"png-bytes",
        ContentType="image/png",
        Metadata={
            "report-id": report_id,
            "upload-id": upload_id,
            "purpose": "resolution",
        },
    )

    call(
        submit_resolution,
        repository,
        tokens,
        workflow,
        report_id,
        {
            "evidence": {
                "objectKey": object_key,
                "uploadId": upload_id,
                "contentType": "image/png",
                "contentLength": 9,
            },
            "note": "Completed.",
            "expectedVersion": 5,
        },
        storage=EvidenceStorage(EVIDENCE_BUCKET, client=s3),
    )

    assert s3.head_object(Bucket=EVIDENCE_BUCKET, Key=f"resolution/{report_id}/{upload_id}.png")


def test_client_helpers_use_the_configured_bucket(dynamodb) -> None:
    client = boto3.client("s3", region_name="us-east-1")
    store = EvidenceStorage(EVIDENCE_BUCKET, client=client)
    assert store.bucket == EVIDENCE_BUCKET
