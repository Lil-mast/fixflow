"""``POST /admin/reports/{reportId}/decision`` - review callback.

The handler performs the real report transition, appends the event, and consumes the
review token in one DynamoDB transaction. Only then does it resume the Step Functions
task. That order keeps a token from being consumed without a recorded decision.
"""

from __future__ import annotations

from typing import Any

import boto3

from shared.auth import admin_identity
from shared.clock import now_iso
from shared.config import load_settings
from shared.errors import (
    Conflict,
    InvalidStateTransition,
    NotFound,
    ReviewExpired,
)
from shared.http import handle_api, parse_json_body, path_parameter
from shared.logging import log
from shared.repository import (
    InvalidStatus,
    ReportNotFound,
    ReportRepository,
    ReviewTokenUnavailable,
    VersionConflict,
    WorkflowTokenRepository,
)
from shared.responses import json_response
from shared.transitions import fields_for_target, public_message, transition_event
from shared.validation import validate_decision_request
from shared.workflow import WorkflowClient

DECISION_RULES = {
    "APPROVE": {
        "target": "APPROVED",
        "action": "REVIEW_APPROVED",
        "sources": {"REVIEW_PENDING", "REVIEW_EXPIRED"},
    },
    "REJECT": {
        "target": "REJECTED",
        "action": "REVIEW_REJECTED",
        "sources": {"REVIEW_PENDING", "REVIEW_EXPIRED", "NEEDS_INFO"},
    },
    "REQUEST_INFO": {
        "target": "NEEDS_INFO",
        "action": "INFO_REQUESTED",
        "sources": {"REVIEW_PENDING", "REVIEW_EXPIRED", "NEEDS_INFO"},
    },
}

WAITING_STATUSES = {"REVIEW_PENDING", "REVIEW_EXPIRED"}


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
        request = validate_decision_request(parse_json_body(event))
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

        rule = DECISION_RULES[request.decision]
        source_status = report["status"]
        if source_status not in rule["sources"]:
            raise InvalidStateTransition(
                f"{request.decision} is not allowed while the report is {source_status}.",
                {"status": source_status},
            )
        if int(report["version"]) != request.expected_version:
            raise Conflict(
                "The report changed since it was loaded. Refetch and retry.",
                {
                    "expectedVersion": request.expected_version,
                    "currentVersion": int(report["version"]),
                },
            )

        task_token = None
        if source_status in WAITING_STATUSES:
            pending = token_store.get_pending_token(report_id, "REVIEW")
            if pending is None:
                raise ReviewExpired()
            task_token = pending

        target_status = rule["target"]
        timestamp = now_iso()
        fields = fields_for_target(target_status, report, timestamp)
        remove_fields: list[str] = []
        if target_status == "NEEDS_INFO":
            fields["reasonCode"] = "INFO_REQUIRED"
        else:
            remove_fields.extend(["reasonCode", "requiredActor"])
        if report.get("workflowExecutionArn"):
            fields["workflowExecutionArn"] = report["workflowExecutionArn"]

        message = public_message(str(rule["action"]), request.public_message)
        event_item = transition_event(
            report_id=report_id,
            previous_status=source_status,
            new_status=target_status,
            action=str(rule["action"]),
            timestamp=timestamp,
            actor_type="ADMIN",
            actor_id=identity.subject,
            visibility="PUBLIC",
            public_message_value=message,
            comment=request.comment,
        )

        try:
            updated = repo.transition_report(
                report_id=report_id,
                expected_statuses=(source_status,),
                target_status=target_status,
                fields=fields,
                event=event_item,
                expected_version=request.expected_version,
                remove_fields=remove_fields,
                allow_same_status=source_status == target_status,
                consume_token=(
                    ("REVIEW", int(task_token["taskVersion"])) if task_token is not None else None
                ),
                consumed_by=identity.subject,
                now=timestamp,
            )
        except ReportNotFound as exc:
            raise NotFound("No report matches that identifier.") from exc
        except VersionConflict as exc:
            raise Conflict("The report changed since it was loaded. Refetch and retry.") from exc
        except InvalidStatus as exc:
            raise InvalidStateTransition(str(exc)) from exc
        except ReviewTokenUnavailable as exc:
            raise ReviewExpired() from exc

        if task_token is not None:
            try:
                flow.send_success(
                    task_token["taskToken"],
                    {"decision": request.decision},
                )
            except ReviewExpired:
                log(
                    "error",
                    "review recorded but workflow token was no longer valid",
                    reportId=report_id,
                )
                raise

        log(
            "info",
            "review decision applied",
            operation="review_report",
            reportId=report_id,
            decision=request.decision,
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
        operation_name="review_report",
    )
