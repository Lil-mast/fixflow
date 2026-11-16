"""Operational metrics calculated from application records.

The metrics endpoint never counts seeded demo records unless the caller explicitly asks.
This module is pure so the definitions are testable without DynamoDB.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shared.clock import now_iso


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _average_seconds(pairs: list[tuple[Any, Any]]) -> int | None:
    durations: list[float] = []
    for start, end in pairs:
        first = _parse_timestamp(start)
        second = _parse_timestamp(end)
        if first is None or second is None:
            continue
        seconds = (second - first).total_seconds()
        if seconds >= 0:
            durations.append(seconds)
    if not durations:
        return None
    return int(sum(durations) / len(durations))


def compute_metrics(
    reports: list[dict[str, Any]],
    *,
    include_demo: bool = False,
    generated_at: str | None = None,
) -> dict[str, Any]:
    selected = [report for report in reports if include_demo or report.get("dataSource") != "DEMO"]

    statuses = [report.get("status") for report in selected]

    return {
        "totalReports": len(selected),
        "awaitingReview": sum(
            1 for status in statuses if status in {"REVIEW_PENDING", "REVIEW_EXPIRED"}
        ),
        "assigned": sum(1 for status in statuses if status == "IN_PROGRESS"),
        "resolved": sum(1 for status in statuses if status == "RESOLVED"),
        "avgSubmissionToReviewSeconds": _average_seconds(
            [
                (report.get("createdAt"), report.get("reviewedAt"))
                for report in selected
                if report.get("reviewedAt")
            ]
        ),
        "avgApprovalToResolutionSeconds": _average_seconds(
            [
                (report.get("approvedAt"), report.get("resolvedAt"))
                for report in selected
                if report.get("approvedAt") and report.get("resolvedAt")
            ]
        ),
        "demoDataIncluded": include_demo,
        "generatedAt": generated_at or now_iso(),
    }
