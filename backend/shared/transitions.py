"""Lifecycle metadata for report transitions.

The repository owns the transactional write; this module owns the product rules that
decide which timestamps and public messages accompany a target status.
"""

from __future__ import annotations

from typing import Any

from shared.ids import new_ulid

STATUSES = frozenset(
    {
        "SUBMITTED",
        "VALIDATING",
        "VALIDATION_FAILED",
        "REVIEW_PENDING",
        "NEEDS_INFO",
        "REJECTED",
        "APPROVED",
        "IN_PROGRESS",
        "RESOLUTION_PENDING",
        "RESOLVED",
        "REVIEW_EXPIRED",
    }
)

SETTABLE_FIELDS = frozenset(
    {
        "approvedAt",
        "assignedAt",
        "assignedTeam",
        "publicSummary",
        "reasonCode",
        "requiredActor",
        "resolutionNote",
        "resolutionRejectionReason",
        "resolutionSubmittedAt",
        "resolvedAt",
        "reviewedAt",
        "workflowExecutionArn",
        "workflowStatus",
    }
)

REMOVABLE_FIELDS = frozenset(
    {
        "reasonCode",
        "requiredActor",
        "resolutionRejectionReason",
    }
)

PUBLIC_MESSAGES = {
    "REVIEW_APPROVED": "Report reviewed and approved for action.",
    "REVIEW_REJECTED": "Report reviewed and closed.",
    "INFO_REQUESTED": "More information is needed from the resident.",
    "TEAM_ASSIGNED": "Assigned to a maintenance team.",
    "RESOLUTION_SUBMITTED": "Resolution evidence submitted for verification.",
    "RESOLUTION_VERIFIED": "Resolution verified by an administrator.",
    "RESOLUTION_REJECTED": "Resolution evidence was not accepted; work continues.",
    "CLARIFICATION_PROVIDED": "Resident provided additional information.",
}


def fields_for_target(target_status: str, report: dict[str, Any], now: str) -> dict[str, Any]:
    """Fields that must be set when entering a status, if they are not already set."""

    fields: dict[str, Any] = {}
    if target_status == "APPROVED":
        fields["reviewedAt"] = report.get("reviewedAt") or now
        fields["approvedAt"] = report.get("approvedAt") or now
    elif target_status in {"REJECTED", "NEEDS_INFO"}:
        fields["reviewedAt"] = report.get("reviewedAt") or now
    elif target_status == "IN_PROGRESS":
        fields["assignedAt"] = report.get("assignedAt") or now
    elif target_status == "RESOLVED":
        fields["resolvedAt"] = now

    if target_status == "NEEDS_INFO":
        fields["requiredActor"] = "RESIDENT"
    return fields


def public_message(action: str, requested: str | None) -> str:
    return requested or PUBLIC_MESSAGES.get(action, "Report updated.")


def transition_event(
    *,
    report_id: str,
    previous_status: str,
    new_status: str,
    action: str,
    timestamp: str,
    actor_type: str = "SYSTEM",
    actor_id: str = "SYSTEM",
    visibility: str = "ADMIN",
    public_message_value: str | None = None,
    comment: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "reportId": report_id,
        "eventId": new_ulid(),
        "actorType": actor_type,
        "actorId": actor_id,
        "action": action,
        "previousStatus": previous_status,
        "newStatus": new_status,
        "timestamp": timestamp,
        "visibility": visibility,
    }
    if public_message_value:
        event["publicMessage"] = public_message_value
    if comment:
        event["comment"] = comment
    if metadata:
        event["metadata"] = metadata
    return event
