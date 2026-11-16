from __future__ import annotations

import pytest

from shared.errors import PayloadTooLarge, UnsupportedMediaType, ValidationError
from shared.ids import new_uuid4
from shared.validation import validate_report_input, validate_upload_request
from tests.support import sample_report_payload


def test_valid_report_is_normalized() -> None:
    payload = sample_report_payload()
    value = validate_report_input(payload)

    assert value.report_id == payload["reportId"].lower()
    assert value.category == "POTHOLE"
    assert value.severity == "HIGH"
    assert value.area == "Kariakoo"
    assert value.latitude == pytest.approx(-6.8189)
    assert value.evidence is None


def test_unknown_top_level_fields_are_rejected() -> None:
    payload = sample_report_payload()
    payload["email"] = "resident@example.test"

    with pytest.raises(ValidationError) as exc:
        validate_report_input(payload)

    assert exc.value.code == "VALIDATION_ERROR"
    assert exc.value.details == {"field": "body", "unknown": ["email"]}


def test_description_length_and_control_characters() -> None:
    short = sample_report_payload(description="too short")
    with pytest.raises(ValidationError):
        validate_report_input(short)

    control = sample_report_payload(description="A pothole\x00 in the road")
    with pytest.raises(ValidationError):
        validate_report_input(control)

    newline = sample_report_payload(
        description="A large pothole in the road.\nIt gets worse after rain."
    )
    assert "\n" in validate_report_input(newline).description


def test_coordinates_must_be_provided_together() -> None:
    payload = sample_report_payload()
    del payload["location"]["longitude"]

    with pytest.raises(ValidationError):
        validate_report_input(payload)


@pytest.mark.parametrize(
    "location",
    [
        {"area": "Kariakoo", "latitude": 91, "longitude": 39},
        {"area": "Kariakoo", "latitude": -6, "longitude": 181},
        {"area": "K", "latitude": -6, "longitude": 39},
    ],
)
def test_bad_locations_are_rejected(location: dict) -> None:
    payload = sample_report_payload()
    payload["location"] = location
    with pytest.raises(ValidationError):
        validate_report_input(payload)


def test_upload_request_validation() -> None:
    request = validate_upload_request(
        {
            "reportId": new_uuid4(),
            "contentType": "image/jpeg",
            "contentLength": 1024,
        }
    )
    assert request.content_type == "image/jpeg"
    assert request.content_length == 1024

    with pytest.raises(UnsupportedMediaType):
        validate_upload_request(
            {
                "reportId": new_uuid4(),
                "contentType": "application/pdf",
                "contentLength": 1024,
            }
        )

    with pytest.raises(PayloadTooLarge):
        validate_upload_request(
            {
                "reportId": new_uuid4(),
                "contentType": "image/png",
                "contentLength": 10 * 1024 * 1024 + 1,
            }
        )


def test_evidence_key_must_belong_to_report() -> None:
    report_id = new_uuid4()
    upload_id = new_uuid4()
    payload = sample_report_payload(report_id)
    payload["evidence"] = {
        "objectKey": f"incoming/{new_uuid4()}/{upload_id}.jpg",
        "uploadId": upload_id,
        "contentType": "image/jpeg",
        "contentLength": 1024,
    }

    with pytest.raises(ValidationError):
        validate_report_input(payload)


def test_evidence_extension_must_match_content_type() -> None:
    report_id = new_uuid4()
    upload_id = new_uuid4()
    payload = sample_report_payload(report_id)
    payload["evidence"] = {
        "objectKey": f"incoming/{report_id}/{upload_id}.png",
        "uploadId": upload_id,
        "contentType": "image/jpeg",
        "contentLength": 1024,
    }

    with pytest.raises(ValidationError):
        validate_report_input(payload)
