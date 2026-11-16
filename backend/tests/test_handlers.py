from __future__ import annotations

import json

from functions.create_upload_url.app import handler as create_upload_url
from functions.get_public_events.app import handler as get_public_events
from functions.get_public_report.app import handler as get_public_report
from functions.submit_report.app import handler as submit_report
from shared.ids import new_uuid4
from shared.repository import ReportRepository
from shared.storage import EvidenceStorage
from tests.support import (
    EVENTS_TABLE,
    EVIDENCE_BUCKET,
    REPORTS_TABLE,
    FakeContext,
    FakeWorkflow,
    api_event,
    make_settings,
    sample_report_payload,
)


def _dependencies():
    settings = make_settings()
    repository = ReportRepository(REPORTS_TABLE, EVENTS_TABLE, region="us-east-1")
    storage = EvidenceStorage(EVIDENCE_BUCKET, region="us-east-1")
    return settings, repository, storage


def _upload(s3, settings, storage, report_id: str) -> dict:
    response = create_upload_url(
        api_event(
            {
                "reportId": report_id,
                "contentType": "image/jpeg",
                "contentLength": 4,
            }
        ),
        FakeContext(),
        settings=settings,
        storage=storage,
    )
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=body["objectKey"],
        Body=b"data",
        ContentType="image/jpeg",
        Metadata={
            "report-id": report_id,
            "upload-id": body["uploadId"],
            "purpose": "report",
        },
    )
    return body


def test_upload_submit_and_public_tracking(dynamodb, s3) -> None:
    settings, repository, storage = _dependencies()
    report_id = new_uuid4()
    upload = _upload(s3, settings, storage, report_id)

    payload = sample_report_payload(report_id)
    payload["evidence"] = {
        "objectKey": upload["objectKey"],
        "uploadId": upload["uploadId"],
        "contentType": "image/jpeg",
        "contentLength": 4,
    }

    response = submit_report(
        api_event(payload),
        FakeContext(),
        settings=settings,
        repository=repository,
        storage=storage,
    )
    assert response["statusCode"] == 201
    created = json.loads(response["body"])
    assert created["status"] == "SUBMITTED"
    assert created["trackingUrl"] == (f"https://app.example.test/track/{created['publicRef']}")

    retry = submit_report(
        api_event(payload),
        FakeContext(),
        settings=settings,
        repository=repository,
        storage=storage,
    )
    assert retry["statusCode"] == 200
    assert json.loads(retry["body"])["publicRef"] == created["publicRef"]

    compact_ref = created["publicRef"].replace("-", "").lower()
    public = get_public_report(
        api_event(path_parameters={"publicRef": compact_ref}),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert public["statusCode"] == 200
    projection = json.loads(public["body"])
    assert projection["publicRef"] == created["publicRef"]
    assert projection["status"] == "SUBMITTED"
    assert "reportId" not in projection
    assert "description" not in projection
    assert "location" not in projection
    assert "evidenceKey" not in projection

    events = get_public_events(
        api_event(path_parameters={"publicRef": created["publicRef"]}),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert events["statusCode"] == 200
    timeline = json.loads(events["body"])
    assert timeline["nextCursor"] is None
    assert len(timeline["items"]) == 1
    assert timeline["items"][0]["status"] == "SUBMITTED"
    assert timeline["items"][0]["publicMessage"] == "Report received."


def test_submit_without_evidence(dynamodb) -> None:
    settings, repository, _ = _dependencies()
    response = submit_report(
        api_event(sample_report_payload()),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert response["statusCode"] == 201


def test_submit_with_missing_evidence_is_422(dynamodb, s3) -> None:
    settings, repository, storage = _dependencies()
    report_id = new_uuid4()
    upload = _upload(s3, settings, storage, report_id)
    s3.delete_object(Bucket=EVIDENCE_BUCKET, Key=upload["objectKey"])

    payload = sample_report_payload(report_id)
    payload["evidence"] = {
        "objectKey": upload["objectKey"],
        "uploadId": upload["uploadId"],
        "contentType": "image/jpeg",
        "contentLength": 4,
    }
    response = submit_report(
        api_event(payload),
        FakeContext(),
        settings=settings,
        repository=repository,
        storage=storage,
    )
    assert response["statusCode"] == 422
    assert json.loads(response["body"])["error"]["code"] == "UNPROCESSABLE_ENTITY"


def test_conflicting_retry_is_409(dynamodb) -> None:
    settings, repository, _ = _dependencies()
    report_id = new_uuid4()
    first = sample_report_payload(report_id)
    second = sample_report_payload(
        report_id,
        description="A different problem at the same client-generated identifier.",
    )

    assert (
        submit_report(
            api_event(first),
            FakeContext(),
            settings=settings,
            repository=repository,
        )["statusCode"]
        == 201
    )
    response = submit_report(
        api_event(second),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"]["code"] == "CONFLICT"


def test_upload_url_rejects_unsupported_type(dynamodb, s3) -> None:
    settings, _, storage = _dependencies()
    response = create_upload_url(
        api_event(
            {
                "reportId": new_uuid4(),
                "contentType": "application/pdf",
                "contentLength": 10,
            }
        ),
        FakeContext(),
        settings=settings,
        storage=storage,
    )
    assert response["statusCode"] == 415
    assert response["headers"]["access-control-allow-origin"] == "http://localhost:5173"


def test_unknown_public_ref_is_generic_404(dynamodb) -> None:
    settings, repository, _ = _dependencies()
    response = get_public_report(
        api_event(path_parameters={"publicRef": "FF-0000-0000-0000-0000"}),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert response["statusCode"] == 404
    error = json.loads(response["body"])["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["message"] == "No report matches that reference."


def test_missing_body_is_validation_error(dynamodb) -> None:
    settings, repository, _ = _dependencies()
    response = submit_report(
        api_event(),
        FakeContext(),
        settings=settings,
        repository=repository,
    )
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["error"]["code"] == "VALIDATION_ERROR"


def test_submission_starts_the_workflow_when_configured(dynamodb) -> None:
    _, repository, _ = _dependencies()
    settings = make_settings(
        state_machine_arn="arn:aws:states:us-east-1:123456789012:stateMachine:fixflow"
    )
    workflow = FakeWorkflow()
    payload = sample_report_payload()

    response = submit_report(
        api_event(payload),
        FakeContext(),
        settings=settings,
        repository=repository,
        workflow=workflow,
    )

    assert response["statusCode"] == 201
    assert len(workflow.started) == 1
    report_id, public_ref = workflow.started[0]
    assert report_id == payload["reportId"].lower()
    assert public_ref.startswith("FF-")

    stored = repository.get_report(report_id)
    assert stored is not None
    assert stored["workflowStatus"] == "STARTED"
    assert stored["workflowExecutionArn"].endswith(report_id)


def test_workflow_start_failure_leaves_report_pending(dynamodb) -> None:
    _, repository, _ = _dependencies()
    settings = make_settings(
        state_machine_arn="arn:aws:states:us-east-1:123456789012:stateMachine:fixflow"
    )
    workflow = FakeWorkflow(fail_start=True)
    payload = sample_report_payload()

    response = submit_report(
        api_event(payload),
        FakeContext(),
        settings=settings,
        repository=repository,
        workflow=workflow,
    )

    assert response["statusCode"] == 201
    stored = repository.get_report(payload["reportId"].lower())
    assert stored is not None
    assert stored["workflowStatus"] == "PENDING"
