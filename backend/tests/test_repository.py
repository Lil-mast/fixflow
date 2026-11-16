from __future__ import annotations

import pytest

from shared.domain import build_new_report
from shared.ids import new_public_ref
from shared.repository import DuplicateReport, ReportRepository
from shared.validation import validate_report_input
from tests.support import EVENTS_TABLE, REPORTS_TABLE, sample_report_payload


def make_repository() -> ReportRepository:
    return ReportRepository(REPORTS_TABLE, EVENTS_TABLE)


def test_create_read_and_find_by_public_ref(dynamodb) -> None:
    repository = make_repository()
    value = validate_report_input(sample_report_payload())
    public_ref = new_public_ref()
    built = build_new_report(value, public_ref=public_ref, evidence_key=None)

    repository.create_report(built.report, built.event)

    fetched = repository.get_report(value.report_id)
    assert fetched is not None
    assert fetched["publicRef"] == public_ref
    assert fetched["priority"] == "P2_HIGH"

    found = repository.find_by_public_ref(public_ref)
    assert found is not None
    assert found["reportId"] == value.report_id


def test_duplicate_create_is_reported(dynamodb) -> None:
    repository = make_repository()
    value = validate_report_input(sample_report_payload())
    built = build_new_report(value, public_ref=new_public_ref(), evidence_key=None)
    repository.create_report(built.report, built.event)

    with pytest.raises(DuplicateReport):
        repository.create_report(built.report, built.event)


def test_public_events_filter_admin_visibility(dynamodb) -> None:
    repository = make_repository()
    value = validate_report_input(sample_report_payload())
    built = build_new_report(value, public_ref=new_public_ref(), evidence_key=None)
    repository.create_report(built.report, built.event)

    dynamodb.put_item(
        TableName=EVENTS_TABLE,
        Item={
            "reportId": {"S": value.report_id},
            "eventId": {"S": "01J8Z3Y6Q7K5M2N4P6R8S0T2V5"},
            "actorType": {"S": "ADMIN"},
            "actorId": {"S": "admin-sub"},
            "action": {"S": "REVIEW_APPROVED"},
            "newStatus": {"S": "APPROVED"},
            "timestamp": {"S": "2026-11-14T19:00:00.000Z"},
            "visibility": {"S": "ADMIN"},
            "comment": {"S": "Internal note"},
        },
    )
    dynamodb.put_item(
        TableName=EVENTS_TABLE,
        Item={
            "reportId": {"S": value.report_id},
            "eventId": {"S": "01J8Z3Y6Q7K5M2N4P6R8S0T2V6"},
            "actorType": {"S": "SYSTEM"},
            "actorId": {"S": "SYSTEM"},
            "action": {"S": "VALIDATION_STARTED"},
            "newStatus": {"S": "VALIDATING"},
            "timestamp": {"S": "2026-11-14T19:01:00.000Z"},
            "visibility": {"S": "PUBLIC"},
        },
    )

    events, cursor = repository.list_public_events(value.report_id)

    assert cursor is None
    assert len(events) == 1
    assert events[0]["action"] == "REPORT_SUBMITTED"
    assert events[0]["visibility"] == "PUBLIC"
