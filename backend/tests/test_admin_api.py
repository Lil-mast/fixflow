from __future__ import annotations

import json

from functions.admin_get_report.app import handler as admin_get_report
from functions.admin_list_reports.app import handler as admin_list_reports
from functions.admin_metrics.app import handler as admin_metrics
from shared.repository import ReportRepository
from shared.storage import EvidenceStorage
from tests.support import (
    EVENTS_TABLE,
    EVIDENCE_BUCKET,
    REPORTS_TABLE,
    FakeContext,
    admin_api_event,
    api_event,
    make_settings,
    seed_report,
)


def repository() -> ReportRepository:
    return ReportRepository(REPORTS_TABLE, EVENTS_TABLE)


def call_list(repo, *, query=None, groups=("admin",)):
    return admin_list_reports(
        admin_api_event(query=query, groups=groups),
        FakeContext(),
        settings=make_settings(),
        repository=repo,
    )


def call_detail(repo, report_id, *, groups=("admin",), storage=None):
    return admin_get_report(
        admin_api_event(path_parameters={"reportId": report_id}, groups=groups),
        FakeContext(),
        settings=make_settings(),
        repository=repo,
        storage=storage,
    )


def call_metrics(repo, *, query=None, groups=("admin",)):
    return admin_metrics(
        admin_api_event(query=query, groups=groups),
        FakeContext(),
        settings=make_settings(),
        repository=repo,
    )


def test_list_returns_live_reports_and_hides_demo_records(dynamodb) -> None:
    repo = repository()
    live = seed_report(repo, status="SUBMITTED", version=1)
    seed_report(repo, status="SUBMITTED", version=1, dataSource="DEMO")

    response = call_list(repo)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert [item["reportId"] for item in body["items"]] == [live["reportId"]]
    assert body["nextCursor"] is None
    assert body["items"][0]["dataSource"] == "LIVE"


def test_list_includes_demo_records_when_requested(dynamodb) -> None:
    repo = repository()
    seed_report(repo, status="SUBMITTED", version=1)
    seed_report(repo, status="SUBMITTED", version=1, dataSource="DEMO")

    response = call_list(repo, query={"includeDemo": "true"})

    assert response["statusCode"] == 200
    assert len(json.loads(response["body"])["items"]) == 2


def test_list_filters_by_status_and_category(dynamodb) -> None:
    repo = repository()
    wanted = seed_report(repo, status="IN_PROGRESS", version=5)
    seed_report(repo, status="RESOLVED", version=8)
    seed_report(repo, status="IN_PROGRESS", version=5, category="WASTE")

    response = call_list(repo, query={"status": "IN_PROGRESS", "category": "POTHOLE"})

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert [item["reportId"] for item in body["items"]] == [wanted["reportId"]]


def test_list_paginates_with_an_opaque_cursor(dynamodb) -> None:
    repo = repository()
    seeded = {seed_report(repo, status="SUBMITTED", version=1)["reportId"] for _ in range(3)}

    first = json.loads(call_list(repo, query={"limit": "2"})["body"])
    assert len(first["items"]) == 2
    assert first["nextCursor"]

    second = json.loads(
        call_list(repo, query={"limit": "2", "cursor": first["nextCursor"]})["body"]
    )
    seen = {item["reportId"] for item in first["items"] + second["items"]}
    assert seen == seeded


def test_list_rejects_unknown_status_and_bad_limit(dynamodb) -> None:
    repo = repository()

    bad_status = call_list(repo, query={"status": "ARCHIVED"})
    assert bad_status["statusCode"] == 400
    assert json.loads(bad_status["body"])["error"]["code"] == "VALIDATION_ERROR"

    bad_limit = call_list(repo, query={"limit": "0"})
    assert bad_limit["statusCode"] == 400


def test_list_requires_the_admin_group(dynamodb) -> None:
    repo = repository()

    missing = admin_list_reports(
        api_event(), FakeContext(), settings=make_settings(), repository=repo
    )
    assert missing["statusCode"] == 401

    forbidden = call_list(repo, groups=("resident",))
    assert forbidden["statusCode"] == 403


def test_detail_returns_history_without_private_fields(dynamodb) -> None:
    repo = repository()
    report = seed_report(repo, status="IN_PROGRESS", version=5)
    report_id = report["reportId"]

    response = call_detail(repo, report_id)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["reportId"] == report_id
    assert body["publicRef"] == report["publicRef"]
    assert body["version"] == 5
    assert len(body["events"]) == 1
    assert body["events"][0]["action"] == "REPORT_SUBMITTED"

    serialized = json.dumps(body)
    for forbidden in (
        "evidenceKey",
        "resolutionEvidenceKey",
        "requestHash",
        "workflowExecutionArn",
        "pendingTaskToken",
        "GSI1PK",
    ):
        assert forbidden not in serialized


def test_detail_generates_a_short_lived_evidence_url(dynamodb, s3) -> None:
    repo = repository()
    report = seed_report(
        repo,
        status="SUBMITTED",
        version=1,
        evidenceKey="evidence/example/photo.jpg",
        evidenceContentType="image/jpeg",
    )

    response = call_detail(
        repo,
        report["reportId"],
        storage=EvidenceStorage(EVIDENCE_BUCKET, client=s3),
    )

    assert response["statusCode"] == 200
    evidence = json.loads(response["body"])["evidence"]
    assert evidence["contentType"] == "image/jpeg"
    assert evidence["url"].startswith("https://")
    assert "Expires=" in evidence["url"]
    assert evidence["expiresAt"]


def test_detail_returns_not_found_for_unknown_report(dynamodb) -> None:
    repo = repository()

    response = call_detail(repo, "0b7f7c1a-1c1e-4d1c-9d1c-1c1e4d1c9d1c")

    assert response["statusCode"] == 404


def test_metrics_counts_live_reports_and_ignores_demo(dynamodb) -> None:
    repo = repository()
    seed_report(
        repo,
        status="RESOLVED",
        version=9,
        reviewedAt="2026-11-14T19:00:00.000Z",
        approvedAt="2026-11-14T19:05:00.000Z",
        resolvedAt="2026-11-14T21:05:00.000Z",
    )
    seed_report(repo, status="IN_PROGRESS", version=5)
    seed_report(repo, status="REVIEW_PENDING", version=2)
    seed_report(repo, status="RESOLVED", version=9, dataSource="DEMO")

    response = call_metrics(repo)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["totalReports"] == 3
    assert body["resolved"] == 1
    assert body["assigned"] == 1
    assert body["awaitingReview"] == 1
    assert body["demoDataIncluded"] is False
    assert body["avgApprovalToResolutionSeconds"] == 7200


def test_metrics_can_include_demo_records(dynamodb) -> None:
    repo = repository()
    seed_report(repo, status="RESOLVED", version=9)
    seed_report(repo, status="RESOLVED", version=9, dataSource="DEMO")

    response = call_metrics(repo, query={"includeDemo": "true"})

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["totalReports"] == 2
    assert body["demoDataIncluded"] is True


def test_metrics_requires_the_admin_group(dynamodb) -> None:
    repo = repository()

    missing = admin_metrics(api_event(), FakeContext(), settings=make_settings(), repository=repo)
    assert missing["statusCode"] == 401

    forbidden = call_metrics(repo, groups=("resident",))
    assert forbidden["statusCode"] == 403
