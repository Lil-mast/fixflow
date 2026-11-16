"""Workflow step: check a stored report before review.

Returns a result object instead of raising for a business validation failure so the state
machine can branch with a Choice state. Infrastructure errors still raise and are retried
or caught by Step Functions.
"""

from __future__ import annotations

from typing import Any

from shared.config import load_settings
from shared.logging import log
from shared.repository import ReportRepository
from shared.validation import report_record_reason


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()
    report_id = event.get("reportId")
    if not isinstance(report_id, str) or not report_id:
        raise ValueError("reportId is required")

    repo = repository or ReportRepository(
        runtime.reports_table,
        runtime.events_table,
        region=runtime.region,
    )
    report = repo.get_report(report_id)
    if report is None:
        reason = "REPORT_NOT_FOUND"
    else:
        reason = report_record_reason(report)

    log(
        "info" if reason is None else "warning",
        "workflow validation finished",
        reportId=report_id,
        valid=reason is None,
        reasonCode=reason,
    )
    return {
        "reportId": report_id,
        "valid": reason is None,
        "reasonCode": reason,
    }
