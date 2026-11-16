"""Workflow step: apply a lifecycle transition and append its event.

The state machine supplies the target status, the legal source statuses, and the event
action. The repository performs the conditional update and event insert in one
transaction. Re-running the step after the target is already reached is a successful
no-op.
"""

from __future__ import annotations

from typing import Any

from shared.clock import now_iso
from shared.config import load_settings
from shared.logging import log
from shared.repository import ReportRepository
from shared.transitions import fields_for_target, public_message, transition_event


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()
    report_id = event.get("reportId")
    target_status = event.get("targetStatus")
    action = event.get("action")
    expected_statuses = event.get("expectedStatuses") or []
    if not isinstance(report_id, str) or not report_id:
        raise ValueError("reportId is required")
    if not isinstance(target_status, str) or not target_status:
        raise ValueError("targetStatus is required")
    if not isinstance(action, str) or not action:
        raise ValueError("action is required")
    if not isinstance(expected_statuses, list) or not expected_statuses:
        raise ValueError("expectedStatuses is required")

    repo = repository or ReportRepository(
        runtime.reports_table,
        runtime.events_table,
        tokens_table=runtime.tokens_table,
        region=runtime.region,
    )
    report = repo.get_report(report_id)
    if report is None:
        raise ValueError("report not found")

    timestamp = now_iso()
    fields = fields_for_target(target_status, report, timestamp)
    execution_arn = event.get("executionArn")
    if isinstance(execution_arn, str) and execution_arn:
        fields["workflowExecutionArn"] = execution_arn

    if target_status == "NEEDS_INFO":
        fields["reasonCode"] = event.get("reasonCode") or "INFO_REQUIRED"
    elif target_status in {"VALIDATION_FAILED", "REVIEW_EXPIRED"}:
        fields["reasonCode"] = event.get("reasonCode") or target_status

    remove_fields: list[str] = []
    if target_status not in {"NEEDS_INFO", "VALIDATION_FAILED", "REVIEW_EXPIRED"}:
        remove_fields.extend(["reasonCode", "requiredActor"])
    if target_status == "REVIEW_PENDING":
        remove_fields.extend(["requiredActor", "reasonCode"])

    visibility = event.get("visibility", "ADMIN")
    message = None
    if visibility == "PUBLIC":
        message = public_message(action, event.get("publicMessage"))

    event_item = transition_event(
        report_id=report_id,
        previous_status=report["status"],
        new_status=target_status,
        action=action,
        timestamp=timestamp,
        actor_type=event.get("actorType", "SYSTEM"),
        actor_id=event.get("actorId", "SYSTEM"),
        visibility=visibility,
        public_message_value=message,
        comment=event.get("comment"),
    )

    updated = repo.transition_report(
        report_id=report_id,
        expected_statuses=tuple(expected_statuses),
        target_status=target_status,
        fields=fields,
        event=event_item,
        remove_fields=remove_fields,
        allow_same_status=bool(event.get("allowSameStatus")),
        ignore_statuses=tuple(event.get("ignoreIfStatus") or ()),
        now=timestamp,
    )
    log(
        "info",
        "report transition applied",
        reportId=report_id,
        status=updated["status"],
        version=updated["version"],
        action=action,
    )
    return {
        "reportId": report_id,
        "status": updated["status"],
        "version": int(updated["version"]),
    }
