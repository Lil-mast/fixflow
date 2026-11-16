"""Workflow step: apply the published deterministic priority rules."""

from __future__ import annotations

from typing import Any

from shared.clock import now_iso
from shared.config import load_settings
from shared.ids import new_ulid
from shared.logging import log
from shared.priority import calculate_priority
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

    repo = repository or ReportRepository(
        runtime.reports_table,
        runtime.events_table,
        region=runtime.region,
    )
    report = repo.get_report(report_id)
    if report is None:
        raise ValueError("report not found")

    score, priority = calculate_priority(report["category"], report["severity"])
    timestamp = now_iso()
    event_item = {
        "reportId": report_id,
        "eventId": new_ulid(),
        "actorType": "SYSTEM",
        "actorId": "SYSTEM",
        "action": "PRIORITY_CALCULATED",
        "previousStatus": report["status"],
        "newStatus": report["status"],
        "timestamp": timestamp,
        "visibility": "ADMIN",
        "metadata": {
            "category": report["category"],
            "severity": report["severity"],
            "priorityScore": score,
            "priority": priority,
        },
    }
    repo.set_priority(
        report_id=report_id,
        score=score,
        priority=priority,
        event=event_item,
        now=timestamp,
    )
    log(
        "info",
        "priority calculated",
        reportId=report_id,
        priority=priority,
        priorityScore=score,
    )
    return {"reportId": report_id, "priority": priority, "priorityScore": score}
