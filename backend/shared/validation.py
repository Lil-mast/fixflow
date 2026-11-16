"""Request validation for the public API.

Validation happens before any database or S3 write. The rules mirror ``docs/api.md`` and
are intentionally strict: unknown fields are rejected so a client cannot smuggle resident
contact data into a report record.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from shared.config import MAX_UPLOAD_BYTES
from shared.errors import (
    PayloadTooLarge,
    UnsupportedMediaType,
    ValidationError,
)
from shared.ids import is_uuid4
from shared.transitions import STATUSES

CATEGORIES = (
    "POTHOLE",
    "STREETLIGHT",
    "WATER_LEAK",
    "DRAINAGE",
    "WASTE",
    "OTHER",
)

SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ("jpg", "jpeg"),
    "image/png": ("png",),
    "image/webp": ("webp",),
}

CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EVIDENCE_KEY_RE = re.compile(
    r"^incoming/(?P<report_id>[0-9a-f-]{36})/"
    r"(?P<upload_id>[0-9a-f-]{36})\.(?P<extension>jpg|jpeg|png|webp)$",
    re.IGNORECASE,
)
RESOLUTION_KEY_RE = re.compile(
    r"^incoming/resolution/(?P<report_id>[0-9a-f-]{36})/"
    r"(?P<upload_id>[0-9a-f-]{36})\.(?P<extension>jpg|jpeg|png|webp)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class UploadRequest:
    report_id: str
    content_type: str
    content_length: int


@dataclass(frozen=True)
class EvidenceInput:
    object_key: str
    upload_id: str
    content_type: str
    content_length: int


@dataclass(frozen=True)
class ReportInput:
    report_id: str
    category: str
    description: str
    severity: str
    area: str
    latitude: float | None
    longitude: float | None
    evidence: EvidenceInput | None


@dataclass(frozen=True)
class DecisionRequest:
    decision: str
    comment: str | None
    public_message: str | None
    expected_version: int


@dataclass(frozen=True)
class AssignRequest:
    assigned_team: str
    comment: str | None
    public_message: str | None
    expected_version: int


@dataclass(frozen=True)
class ResolutionRequest:
    evidence: EvidenceInput
    note: str
    public_message: str | None
    expected_version: int


@dataclass(frozen=True)
class VerificationRequest:
    decision: str
    comment: str | None
    public_message: str | None
    expected_version: int


@dataclass(frozen=True)
class AdminListQuery:
    status: str | None
    category: str | None
    limit: int
    cursor: str | None
    include_demo: bool


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError(f"{field} must be an object.", {"field": field})
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValidationError(
            f"Unknown field(s): {', '.join(unknown)}.",
            {"field": field, "unknown": unknown},
        )


def _required(value: dict[str, Any], field: str) -> Any:
    if field not in value:
        raise ValidationError(f"{field} is required.", {"field": field})
    return value[field]


def _string(
    value: Any,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string.", {"field": field})
    trimmed = value.strip()
    if CONTROL_CHARS_RE.search(trimmed):
        raise ValidationError(f"{field} contains control characters.", {"field": field})
    if len(trimmed) < minimum or len(trimmed) > maximum:
        raise ValidationError(
            f"{field} must be between {minimum} and {maximum} characters.",
            {"field": field},
        )
    return trimmed


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field} must be an integer.", {"field": field})
    if value <= 0:
        raise ValidationError(f"{field} must be greater than zero.", {"field": field})
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field} must be a number.", {"field": field})
    number = float(value)
    if not math.isfinite(number):
        raise ValidationError(f"{field} must be finite.", {"field": field})
    return number


def _enum(value: Any, field: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(
            f"{field} must be one of: {', '.join(allowed)}.",
            {"field": field},
        )
    return value


def _content_type(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in CONTENT_TYPE_EXTENSIONS:
        raise UnsupportedMediaType("Only JPEG, PNG, and WebP images are supported.")
    return value


def validate_upload_request(payload: Any) -> UploadRequest:
    body = _require_mapping(payload, "body")
    _reject_unknown(body, {"reportId", "contentType", "contentLength"}, "body")

    report_id = _required(body, "reportId")
    if not is_uuid4(report_id):
        raise ValidationError("reportId must be a UUIDv4.", {"field": "reportId"})

    content_type = _content_type(_required(body, "contentType"), "contentType")
    content_length = _positive_int(_required(body, "contentLength"), "contentLength")
    if content_length > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("Evidence must be 10 MiB or smaller.")

    return UploadRequest(
        report_id=report_id.lower(),
        content_type=content_type,
        content_length=content_length,
    )


def validate_resolution_upload_request(payload: Any, report_id: str) -> UploadRequest:
    if not is_uuid4(report_id):
        raise ValidationError("reportId must be a UUIDv4.", {"field": "reportId"})

    body = _require_mapping(payload, "body")
    _reject_unknown(body, {"contentType", "contentLength"}, "body")

    content_type = _content_type(_required(body, "contentType"), "contentType")
    content_length = _positive_int(_required(body, "contentLength"), "contentLength")
    if content_length > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("Evidence must be 10 MiB or smaller.")

    return UploadRequest(
        report_id=report_id.lower(),
        content_type=content_type,
        content_length=content_length,
    )


def _validate_location(value: Any) -> tuple[str, float | None, float | None]:
    location = _require_mapping(value, "location")
    _reject_unknown(location, {"area", "latitude", "longitude"}, "location")

    area = _string(_required(location, "area"), "location.area", minimum=2, maximum=120)

    has_latitude = "latitude" in location
    has_longitude = "longitude" in location
    if has_latitude != has_longitude:
        raise ValidationError(
            "location.latitude and location.longitude must be provided together.",
            {"field": "location"},
        )

    if not has_latitude:
        return area, None, None

    latitude = _number(location["latitude"], "location.latitude")
    longitude = _number(location["longitude"], "location.longitude")
    if not -90 <= latitude <= 90:
        raise ValidationError(
            "location.latitude must be between -90 and 90.",
            {"field": "location.latitude"},
        )
    if not -180 <= longitude <= 180:
        raise ValidationError(
            "location.longitude must be between -180 and 180.",
            {"field": "location.longitude"},
        )
    return area, latitude, longitude


def _validate_evidence(value: Any, report_id: str, *, purpose: str = "report") -> EvidenceInput:
    evidence = _require_mapping(value, "evidence")
    _reject_unknown(
        evidence,
        {"objectKey", "uploadId", "contentType", "contentLength"},
        "evidence",
    )

    object_key = _required(evidence, "objectKey")
    if not isinstance(object_key, str):
        raise ValidationError(
            "evidence.objectKey must be a string.", {"field": "evidence.objectKey"}
        )

    pattern = RESOLUTION_KEY_RE if purpose == "resolution" else EVIDENCE_KEY_RE
    expected_shape = (
        "incoming/resolution/<reportId>/<uploadId>.<ext>"
        if purpose == "resolution"
        else "incoming/<reportId>/<uploadId>.<ext>"
    )
    match = pattern.fullmatch(object_key)
    if match is None:
        raise ValidationError(
            f"evidence.objectKey must match {expected_shape}.",
            {"field": "evidence.objectKey"},
        )

    key_report_id = match.group("report_id").lower()
    key_upload_id = match.group("upload_id").lower()
    if key_report_id != report_id:
        raise ValidationError(
            "evidence.objectKey does not belong to this report.",
            {"field": "evidence.objectKey"},
        )
    if not is_uuid4(key_upload_id):
        raise ValidationError(
            "evidence.objectKey contains an invalid upload ID.",
            {"field": "evidence.objectKey"},
        )

    upload_id = _required(evidence, "uploadId")
    if not is_uuid4(upload_id) or upload_id.lower() != key_upload_id:
        raise ValidationError(
            "evidence.uploadId does not match evidence.objectKey.",
            {"field": "evidence.uploadId"},
        )

    content_type = _content_type(_required(evidence, "contentType"), "evidence.contentType")
    extension = match.group("extension").lower()
    if extension not in CONTENT_TYPE_EXTENSIONS[content_type]:
        raise ValidationError(
            "evidence.objectKey extension does not match evidence.contentType.",
            {"field": "evidence.objectKey"},
        )

    content_length = _positive_int(_required(evidence, "contentLength"), "evidence.contentLength")
    if content_length > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge("Evidence must be 10 MiB or smaller.")

    return EvidenceInput(
        object_key=object_key,
        upload_id=key_upload_id,
        content_type=content_type,
        content_length=content_length,
    )


def validate_report_input(payload: Any) -> ReportInput:
    body = _require_mapping(payload, "body")
    _reject_unknown(
        body,
        {"reportId", "category", "description", "severity", "location", "evidence"},
        "body",
    )

    report_id_value = _required(body, "reportId")
    if not is_uuid4(report_id_value):
        raise ValidationError("reportId must be a UUIDv4.", {"field": "reportId"})
    report_id = report_id_value.lower()

    category = _enum(_required(body, "category"), "category", CATEGORIES)
    description = _string(_required(body, "description"), "description", minimum=10, maximum=2000)
    severity = _enum(_required(body, "severity"), "severity", SEVERITIES)

    area, latitude, longitude = _validate_location(_required(body, "location"))

    evidence = None
    if body.get("evidence") is not None:
        evidence = _validate_evidence(body["evidence"], report_id)

    return ReportInput(
        report_id=report_id,
        category=category,
        description=description,
        severity=severity,
        area=area,
        latitude=latitude,
        longitude=longitude,
        evidence=evidence,
    )


DECISIONS = ("APPROVE", "REJECT", "REQUEST_INFO")


def validate_decision_request(payload: Any) -> DecisionRequest:
    body = _require_mapping(payload, "body")
    _reject_unknown(
        body,
        {"decision", "comment", "publicMessage", "expectedVersion"},
        "body",
    )

    decision = _enum(_required(body, "decision"), "decision", DECISIONS)

    comment = None
    if body.get("comment") is not None:
        comment = _string(body["comment"], "comment", minimum=1, maximum=2000)
    if decision in {"REJECT", "REQUEST_INFO"} and comment is None:
        raise ValidationError(
            "comment is required for REJECT and REQUEST_INFO.",
            {"field": "comment"},
        )

    public_message = None
    if body.get("publicMessage") is not None:
        public_message = _string(body["publicMessage"], "publicMessage", minimum=1, maximum=280)

    expected_version = _positive_int(_required(body, "expectedVersion"), "expectedVersion")

    return DecisionRequest(
        decision=decision,
        comment=comment,
        public_message=public_message,
        expected_version=expected_version,
    )


def _optional_comment(body: dict[str, Any]) -> str | None:
    if body.get("comment") is None:
        return None
    return _string(body["comment"], "comment", minimum=1, maximum=2000)


def _optional_public_message(body: dict[str, Any]) -> str | None:
    if body.get("publicMessage") is None:
        return None
    return _string(body["publicMessage"], "publicMessage", minimum=1, maximum=280)


def validate_assign_request(payload: Any) -> AssignRequest:
    body = _require_mapping(payload, "body")
    _reject_unknown(
        body,
        {"assignedTeam", "comment", "publicMessage", "expectedVersion"},
        "body",
    )

    assigned_team = _string(_required(body, "assignedTeam"), "assignedTeam", minimum=2, maximum=80)

    return AssignRequest(
        assigned_team=assigned_team,
        comment=_optional_comment(body),
        public_message=_optional_public_message(body),
        expected_version=_positive_int(_required(body, "expectedVersion"), "expectedVersion"),
    )


def validate_resolution_request(payload: Any, report_id: str) -> ResolutionRequest:
    body = _require_mapping(payload, "body")
    _reject_unknown(
        body,
        {"evidence", "note", "publicMessage", "expectedVersion"},
        "body",
    )

    evidence = _validate_evidence(_required(body, "evidence"), report_id, purpose="resolution")
    note = _string(_required(body, "note"), "note", minimum=1, maximum=2000)

    return ResolutionRequest(
        evidence=evidence,
        note=note,
        public_message=_optional_public_message(body),
        expected_version=_positive_int(_required(body, "expectedVersion"), "expectedVersion"),
    )


VERIFICATION_DECISIONS = ("VERIFIED", "REJECTED")


def validate_verification_request(payload: Any) -> VerificationRequest:
    body = _require_mapping(payload, "body")
    _reject_unknown(
        body,
        {"decision", "comment", "publicMessage", "expectedVersion"},
        "body",
    )

    decision = _enum(_required(body, "decision"), "decision", VERIFICATION_DECISIONS)
    comment = _optional_comment(body)
    if decision == "REJECTED" and comment is None:
        raise ValidationError("comment is required for REJECTED.", {"field": "comment"})

    return VerificationRequest(
        decision=decision,
        comment=comment,
        public_message=_optional_public_message(body),
        expected_version=_positive_int(_required(body, "expectedVersion"), "expectedVersion"),
    )


def _boolean(value: Any, field: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise ValidationError(f"{field} must be true or false.", {"field": field})


def validate_admin_list_query(query: Any) -> AdminListQuery:
    parameters = query if isinstance(query, dict) else {}

    status = parameters.get("status")
    if status is not None and status not in STATUSES:
        raise ValidationError("status must be a lifecycle status.", {"field": "status"})

    category = parameters.get("category")
    if category is not None and category not in CATEGORIES:
        raise ValidationError("category must be a report category.", {"field": "category"})

    limit = 25
    raw_limit = parameters.get("limit")
    if raw_limit is not None:
        if not isinstance(raw_limit, str) or not raw_limit.isdigit():
            raise ValidationError("limit must be an integer between 1 and 100.", {"field": "limit"})
        limit = int(raw_limit)
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100.", {"field": "limit"})

    cursor = parameters.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ValidationError("cursor must be a string.", {"field": "cursor"})

    return AdminListQuery(
        status=status,
        category=category,
        limit=limit,
        cursor=cursor or None,
        include_demo=_boolean(parameters.get("includeDemo"), "includeDemo"),
    )


def validate_metrics_query(query: Any) -> bool:
    parameters = query if isinstance(query, dict) else {}
    return _boolean(parameters.get("includeDemo"), "includeDemo")


def _stored_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def report_record_reason(report: dict[str, Any]) -> str | None:
    """Return a stable reason code when a stored report is not workflow-valid.

    Submission already validates the request. This check is a defensive guard against a
    partial write, a manual edit, or a future write path that bypasses validation.
    """

    if report.get("category") not in CATEGORIES:
        return "INVALID_CATEGORY"
    if report.get("severity") not in SEVERITIES:
        return "INVALID_SEVERITY"

    description = report.get("description")
    if not isinstance(description, str) or not 10 <= len(description) <= 2000:
        return "INVALID_DESCRIPTION"

    area = report.get("area")
    if not isinstance(area, str) or not 2 <= len(area) <= 120:
        return "INVALID_AREA"

    location = report.get("location")
    if location is not None:
        if not isinstance(location, dict):
            return "INVALID_COORDINATES"
        latitude = _stored_number(location.get("latitude"))
        longitude = _stored_number(location.get("longitude"))
        if (
            latitude is None
            or longitude is None
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
        ):
            return "INVALID_COORDINATES"

    evidence_key = report.get("evidenceKey")
    if evidence_key is not None:
        if not isinstance(evidence_key, str) or not evidence_key.startswith("evidence/"):
            return "INVALID_EVIDENCE_METADATA"
        if report.get("evidenceContentType") not in CONTENT_TYPE_EXTENSIONS:
            return "INVALID_EVIDENCE_METADATA"
        size = _stored_number(report.get("evidenceSize"))
        if size is None or not 1 <= size <= MAX_UPLOAD_BYTES:
            return "INVALID_EVIDENCE_METADATA"

    if not isinstance(report.get("publicRef"), str):
        return "MISSING_PUBLIC_REF"
    return None
