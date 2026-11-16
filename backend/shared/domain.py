"""Build report records, events, and the public projection.

Domain objects are plain dictionaries because that is what DynamoDB stores. Keeping the
shape in one module makes it easier to audit which fields are public and which never leave
the admin boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.clock import now_iso
from shared.ids import canonical_json, new_ulid, sha256_hex
from shared.priority import calculate_priority
from shared.validation import ReportInput

STATUS_SUBMITTED = "SUBMITTED"


@dataclass(frozen=True)
class NewReport:
    report: dict
    event: dict
    request_hash: str


def request_fingerprint(value: ReportInput) -> str:
    """Hash the canonical create request, ignoring transport and timestamp details."""

    payload = {
        "reportId": value.report_id,
        "category": value.category,
        "description": value.description,
        "severity": value.severity,
        "location": {
            "area": value.area,
            "latitude": value.latitude,
            "longitude": value.longitude,
        },
        "evidence": None,
    }
    if value.evidence is not None:
        payload["evidence"] = {
            "objectKey": value.evidence.object_key,
            "uploadId": value.evidence.upload_id,
            "contentType": value.evidence.content_type,
            "contentLength": value.evidence.content_length,
        }
    return sha256_hex(canonical_json(payload))


def build_new_report(
    value: ReportInput,
    *,
    public_ref: str,
    evidence_key: str | None,
    created_at: str | None = None,
) -> NewReport:
    created = created_at or now_iso()
    score, priority = calculate_priority(value.category, value.severity)
    request_hash = request_fingerprint(value)
    sort_value = f"{created}#{value.report_id}"

    report: dict = {
        "reportId": value.report_id,
        "publicRef": public_ref,
        "requestHash": request_hash,
        "category": value.category,
        "description": value.description,
        "severity": value.severity,
        "priorityScore": score,
        "priority": priority,
        "status": STATUS_SUBMITTED,
        "version": 1,
        "area": value.area,
        "publicSummary": "Report received.",
        "workflowStatus": "PENDING",
        "dataSource": "LIVE",
        "createdAt": created,
        "updatedAt": created,
        "GSI1PK": f"STATUS#{STATUS_SUBMITTED}",
        "GSI1SK": sort_value,
        "GSI2PK": f"PUBLIC#{public_ref}",
        "GSI2SK": "REPORT",
        "GSI3PK": "ALL",
        "GSI3SK": sort_value,
    }
    if value.latitude is not None and value.longitude is not None:
        report["location"] = {
            "latitude": value.latitude,
            "longitude": value.longitude,
        }
    if value.evidence is not None and evidence_key is not None:
        report["evidenceKey"] = evidence_key
        report["evidenceContentType"] = value.evidence.content_type
        report["evidenceSize"] = value.evidence.content_length

    event = {
        "reportId": value.report_id,
        "eventId": new_ulid(),
        "actorType": "RESIDENT",
        "actorId": "RESIDENT",
        "action": "REPORT_SUBMITTED",
        "newStatus": STATUS_SUBMITTED,
        "timestamp": created,
        "visibility": "PUBLIC",
        "publicMessage": "Report received.",
    }

    return NewReport(report=report, event=event, request_hash=request_hash)


def public_report_projection(report: dict) -> dict:
    """Return only the fields a resident is allowed to see."""

    return {
        "publicRef": report["publicRef"],
        "category": report["category"],
        "area": report["area"],
        "status": report["status"],
        "priority": report.get("priority"),
        "submittedAt": report["createdAt"],
        "resolvedAt": report.get("resolvedAt"),
        "publicSummary": report.get("publicSummary"),
        "lastUpdatedAt": report["updatedAt"],
    }


def public_event_projection(event: dict) -> dict:
    return {
        "eventId": event["eventId"],
        "status": event["newStatus"],
        "timestamp": event["timestamp"],
        "publicMessage": event.get("publicMessage"),
    }
