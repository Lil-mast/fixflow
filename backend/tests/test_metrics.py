from __future__ import annotations

from shared.metrics import compute_metrics


def test_empty_dataset_reports_zero_counts_and_null_averages() -> None:
    metrics = compute_metrics([], generated_at="2026-11-16T00:00:00.000Z")

    assert metrics["totalReports"] == 0
    assert metrics["resolved"] == 0
    assert metrics["avgSubmissionToReviewSeconds"] is None
    assert metrics["avgApprovalToResolutionSeconds"] is None
    assert metrics["generatedAt"] == "2026-11-16T00:00:00.000Z"


def test_counts_group_by_status_bucket() -> None:
    reports = [
        {"status": "REVIEW_PENDING"},
        {"status": "REVIEW_EXPIRED"},
        {"status": "IN_PROGRESS"},
        {"status": "RESOLVED"},
        {"status": "REJECTED"},
    ]

    metrics = compute_metrics(reports, generated_at="2026-11-16T00:00:00.000Z")

    assert metrics["totalReports"] == 5
    assert metrics["awaitingReview"] == 2
    assert metrics["assigned"] == 1
    assert metrics["resolved"] == 1


def test_demo_records_are_excluded_unless_requested() -> None:
    reports = [
        {"status": "RESOLVED", "dataSource": "LIVE"},
        {"status": "RESOLVED", "dataSource": "DEMO"},
    ]

    live_only = compute_metrics(reports, generated_at="2026-11-16T00:00:00.000Z")
    with_demo = compute_metrics(reports, include_demo=True, generated_at="2026-11-16T00:00:00.000Z")

    assert live_only["totalReports"] == 1
    assert live_only["demoDataIncluded"] is False
    assert with_demo["totalReports"] == 2
    assert with_demo["demoDataIncluded"] is True


def test_averages_use_only_complete_pairs() -> None:
    reports = [
        {
            "createdAt": "2026-11-14T18:00:00.000Z",
            "reviewedAt": "2026-11-14T20:00:00.000Z",
            "approvedAt": "2026-11-14T20:30:00.000Z",
            "resolvedAt": "2026-11-15T02:30:00.000Z",
        },
        {
            "createdAt": "2026-11-15T10:00:00.000Z",
            "reviewedAt": "2026-11-15T13:00:00.000Z",
        },
        {"createdAt": "2026-11-15T10:00:00.000Z", "resolvedAt": "2026-11-15T12:00:00.000Z"},
    ]

    metrics = compute_metrics(reports, generated_at="2026-11-16T00:00:00.000Z")

    assert metrics["avgSubmissionToReviewSeconds"] == 9000
    assert metrics["avgApprovalToResolutionSeconds"] == 21600


def test_negative_and_malformed_timestamps_are_ignored() -> None:
    reports = [
        {
            "createdAt": "2026-11-14T20:00:00.000Z",
            "reviewedAt": "2026-11-14T18:00:00.000Z",
        },
        {"createdAt": "not-a-date", "reviewedAt": "2026-11-14T18:00:00.000Z"},
    ]

    metrics = compute_metrics(reports, generated_at="2026-11-16T00:00:00.000Z")

    assert metrics["avgSubmissionToReviewSeconds"] is None
