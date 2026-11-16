from __future__ import annotations

from shared.domain import (
    build_new_report,
    public_event_projection,
    public_report_projection,
    request_fingerprint,
)
from shared.ids import new_public_ref
from shared.validation import validate_report_input
from tests.support import sample_report_payload


def test_new_report_contains_index_keys_and_priority() -> None:
    value = validate_report_input(sample_report_payload())
    public_ref = new_public_ref()
    created = "2026-11-14T18:30:00.000Z"

    built = build_new_report(
        value,
        public_ref=public_ref,
        evidence_key=None,
        created_at=created,
    )
    report = built.report

    assert report["status"] == "SUBMITTED"
    assert report["priority"] == "P2_HIGH"
    assert report["priorityScore"] == 50
    assert report["version"] == 1
    assert report["workflowStatus"] == "PENDING"
    assert report["GSI1PK"] == "STATUS#SUBMITTED"
    assert report["GSI1SK"] == f"{created}#{value.report_id}"
    assert report["GSI2PK"] == f"PUBLIC#{public_ref}"
    assert report["GSI2SK"] == "REPORT"
    assert report["GSI3PK"] == "ALL"
    assert report["location"] == {"latitude": -6.8189, "longitude": 39.2803}
    assert report["requestHash"] == request_fingerprint(value)
    assert built.event["action"] == "REPORT_SUBMITTED"
    assert built.event["visibility"] == "PUBLIC"


def test_fingerprint_ignores_whitespace_but_not_content() -> None:
    first = validate_report_input(sample_report_payload())
    second_payload = sample_report_payload(
        report_id=first.report_id,
        description="  "
        + "Large pothole in the left lane, about 40 cm wide, causing cars to swerve.  ",
    )
    second = validate_report_input(second_payload)
    assert request_fingerprint(first) == request_fingerprint(second)

    different = sample_report_payload(
        report_id=first.report_id,
        description="A different pothole description that is long enough.",
    )
    assert request_fingerprint(first) != request_fingerprint(validate_report_input(different))


def test_public_projection_never_includes_private_fields() -> None:
    value = validate_report_input(sample_report_payload())
    built = build_new_report(
        value,
        public_ref=new_public_ref(),
        evidence_key="evidence/report/upload.jpg",
    )
    built.report["description"] = "Resident description with a gate code 1234."
    built.report["location"] = {"latitude": -6.8, "longitude": 39.2}
    built.report["evidenceKey"] = "evidence/private.jpg"

    projection = public_report_projection(built.report)

    assert set(projection) == {
        "publicRef",
        "category",
        "area",
        "status",
        "priority",
        "submittedAt",
        "resolvedAt",
        "publicSummary",
        "lastUpdatedAt",
    }
    assert "reportId" not in projection
    assert "description" not in projection
    assert "location" not in projection
    assert "evidenceKey" not in projection


def test_event_projection_only_exposes_public_fields() -> None:
    projection = public_event_projection(
        {
            "eventId": "01J8Z3Y6Q7K5M2N4P6R8S0T2V4",
            "newStatus": "SUBMITTED",
            "timestamp": "2026-11-14T18:30:00.000Z",
            "publicMessage": "Report received.",
            "comment": "admin-only note",
            "actorId": "admin-sub",
        }
    )
    assert projection == {
        "eventId": "01J8Z3Y6Q7K5M2N4P6R8S0T2V4",
        "status": "SUBMITTED",
        "timestamp": "2026-11-14T18:30:00.000Z",
        "publicMessage": "Report received.",
    }
