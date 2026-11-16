"""``POST /admin/reports/{reportId}/verify`` - verify or reject a resolution."""

from __future__ import annotations

from typing import Any

import boto3

from shared.auth import admin_identity
from shared.callbacks import apply_callback_transition, send_callback_success
from shared.clock import now_iso
from shared.config import load_settings
from shared.errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    TaskExpired,
)
from shared.http import handle_api, parse_json_body, path_parameter
from shared.logging import log
from shared.repository import ReportRepository, WorkflowTokenRepository
from shared.responses import json_response
from shared.transitions import fields_for_target, public_message, transition_event
from shared.validation import validate_verification_request
from shared.workflow import WorkflowClient

RULES = {
    "VERIFIED": {
        "target": "RESOLVED",
        "action": "RESOLUTION_VERIFIED",
        "publicMessage": "Resolution verified by an administrator.",
    },
    "REJECTED": {
        "target": "IN_PROGRESS",
        "action": "RESOLUTION_REJECTED",
        "publicMessage": "Resolution evidence was not accepted; work continues.",
    },
}


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
    tokens: WorkflowTokenRepository | None = None,
    workflow: WorkflowClient | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        identity = admin_identity(event)
        request = validate_verification_request(parse_json_body(event))
        report_id = path_parameter(event, "reportId")

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            tokens_table=runtime.tokens_table,
            region=runtime.region,
        )
        token_store = tokens or WorkflowTokenRepository(runtime.tokens_table, region=runtime.region)
        flow = workflow or WorkflowClient(
            client=boto3.client("stepfunctions", region_name=runtime.region)
        )

        report = repo.get_report(report_id)
        if report is None:
            raise NotFound("No report matches that identifier.")
        if int(report["version"]) != request.expected_version:
            raise Conflict(
                "The report changed since it was loaded. Refetch and retry.",
                {
                    "expectedVersion": request.expected_version,
                    "currentVersion": int(report["version"]),
                },
            )
        if report["status"] != "RESOLUTION_PENDING":
            raise InvalidStateTransition(
                "Verification is only allowed while the report is RESOLUTION_PENDING.",
                {"status": report["status"]},
            )

        pending = token_store.get_pending_token(report_id, "VERIFICATION")
        if pending is None:
            raise TaskExpired("The verification window has expired. Refetch the report and retry.")

        rule = RULES[request.decision]
        target_status = str(rule["target"])
        timestamp = now_iso()
        fields = fields_for_target(target_status, report, timestamp)
        remove_fields: list[str] = []
        if target_status == "IN_PROGRESS":
            fields["resolutionRejectionReason"] = request.comment or ""
        else:
            remove_fields.append("resolutionRejectionReason")

        event_item = transition_event(
            report_id=report_id,
            previous_status=report["status"],
            new_status=target_status,
            action=str(rule["action"]),
            timestamp=timestamp,
            actor_type="ADMIN",
            actor_id=identity.subject,
            visibility="PUBLIC",
            public_message_value=public_message(str(rule["action"]), request.public_message),
            comment=request.comment,
        )

        updated = apply_callback_transition(
            repo,
            report_id=report_id,
            expected_statuses=("RESOLUTION_PENDING",),
            target_status=target_status,
            fields=fields,
            event=event_item,
            expected_version=request.expected_version,
            remove_fields=remove_fields,
            consume_token=("VERIFICATION", int(pending["taskVersion"])),
            consumed_by=identity.subject,
            now=timestamp,
        )

        send_callback_success(
            flow,
            pending["taskToken"],
            {"decision": request.decision},
            report_id=report_id,
        )

        log(
            "info",
            "resolution decision applied",
            operation="verify_resolution",
            reportId=report_id,
            decision=request.decision,
            status=updated["status"],
        )
        body: dict[str, Any] = {
            "reportId": report_id,
            "status": updated["status"],
            "version": int(updated["version"]),
            "updatedAt": updated["updatedAt"],
        }
        if request.decision == "VERIFIED":
            body["resolvedAt"] = updated.get("resolvedAt")
        return json_response(
            200,
            body,
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="verify_resolution",
    )
