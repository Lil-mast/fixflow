"""``POST /admin/reports/{reportId}/resolution`` - submit completion evidence."""

from __future__ import annotations

from typing import Any

import boto3

from shared.auth import admin_identity
from shared.callbacks import apply_callback_transition, send_callback_success
from shared.clock import now_iso
from shared.config import load_settings
from shared.errors import (
    Conflict,
    InternalError,
    InvalidStateTransition,
    NotFound,
    TaskExpired,
)
from shared.http import handle_api, parse_json_body, path_parameter
from shared.logging import log
from shared.repository import ReportRepository, WorkflowTokenRepository
from shared.responses import json_response
from shared.storage import EvidenceStorage
from shared.transitions import fields_for_target, public_message, transition_event
from shared.validation import validate_resolution_request
from shared.workflow import WorkflowClient


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
    tokens: WorkflowTokenRepository | None = None,
    storage: EvidenceStorage | None = None,
    workflow: WorkflowClient | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        identity = admin_identity(event)
        report_id = path_parameter(event, "reportId")
        request = validate_resolution_request(parse_json_body(event), report_id)

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
        if report["status"] != "IN_PROGRESS":
            raise InvalidStateTransition(
                "Resolution evidence is only accepted while the report is IN_PROGRESS.",
                {"status": report["status"]},
            )

        pending = token_store.get_pending_token(report_id, "RESOLUTION")
        if pending is None:
            raise TaskExpired("The resolution window has expired. Refetch the report and retry.")

        store = storage
        if store is None:
            if not runtime.evidence_bucket:
                raise InternalError("Evidence storage is not configured.")
            store = EvidenceStorage(runtime.evidence_bucket, region=runtime.region)
        resolution_key = store.verify_and_copy(request.evidence, report_id, purpose="resolution")

        timestamp = now_iso()
        fields = fields_for_target("RESOLUTION_PENDING", report, timestamp)
        fields.update(
            {
                "resolutionEvidenceKey": resolution_key,
                "resolutionContentType": request.evidence.content_type,
                "resolutionSize": request.evidence.content_length,
                "resolutionNote": request.note,
            }
        )

        event_item = transition_event(
            report_id=report_id,
            previous_status=report["status"],
            new_status="RESOLUTION_PENDING",
            action="RESOLUTION_SUBMITTED",
            timestamp=timestamp,
            actor_type="ADMIN",
            actor_id=identity.subject,
            visibility="PUBLIC",
            public_message_value=public_message("RESOLUTION_SUBMITTED", request.public_message),
        )

        updated = apply_callback_transition(
            repo,
            report_id=report_id,
            expected_statuses=("IN_PROGRESS",),
            target_status="RESOLUTION_PENDING",
            fields=fields,
            event=event_item,
            expected_version=request.expected_version,
            remove_fields=("resolutionRejectionReason",),
            consume_token=("RESOLUTION", int(pending["taskVersion"])),
            consumed_by=identity.subject,
            now=timestamp,
        )

        send_callback_success(
            flow,
            pending["taskToken"],
            {"submitted": True},
            report_id=report_id,
        )

        log(
            "info",
            "resolution submitted",
            operation="submit_resolution",
            reportId=report_id,
            status=updated["status"],
        )
        return json_response(
            200,
            {
                "reportId": report_id,
                "status": updated["status"],
                "version": int(updated["version"]),
                "updatedAt": updated["updatedAt"],
            },
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="submit_resolution",
    )
