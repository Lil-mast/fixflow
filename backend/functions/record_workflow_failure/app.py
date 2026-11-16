"""Workflow step: record an unhandled workflow failure without changing lifecycle status."""

from __future__ import annotations

from typing import Any

from shared.clock import now_iso
from shared.config import load_settings
from shared.ids import new_ulid
from shared.logging import log
from shared.repository import ReportRepository


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

    reason = str(event.get("reason") or "UNHANDLED_WORKFLOW_ERROR")[:120]
    timestamp = now_iso()
    repo = repository or ReportRepository(
        runtime.reports_table,
        runtime.events_table,
        region=runtime.region,
    )
    report = repo.get_report(report_id)
    if report is None:
        raise ValueError("report not found")

    event_item = {
        "reportId": report_id,
        "eventId": new_ulid(),
        "actorType": "SYSTEM",
        "actorId": "SYSTEM",
        "action": "WORKFLOW_FAILED",
        "previousStatus": report["status"],
        "newStatus": report["status"],
        "timestamp": timestamp,
        "visibility": "ADMIN",
        "metadata": {"reasonCode": reason},
    }
    repo.record_workflow_failure(
        report_id=report_id,
        event=event_item,
        execution_arn=event.get("executionArn"),
        now=timestamp,
    )
    log(
        "error",
        "workflow failure recorded",
        reportId=report_id,
        reasonCode=reason,
    )
    return {"reportId": report_id, "workflowStatus": "FAILED"}
