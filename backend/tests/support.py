"""Shared fixtures and builders for backend tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from shared.config import Settings
from shared.domain import build_new_report
from shared.ids import new_public_ref, new_uuid4
from shared.validation import validate_report_input

REPORTS_TABLE = "FixFlowReports"
EVENTS_TABLE = "FixFlowReportEvents"
TOKENS_TABLE = "FixFlowWorkflowTokens"
EVIDENCE_BUCKET = "fixflow-evidence-test"


def make_settings(**overrides: Any) -> Settings:
    base = Settings(
        stage="test",
        region="us-east-1",
        reports_table=REPORTS_TABLE,
        events_table=EVENTS_TABLE,
        tokens_table=TOKENS_TABLE,
        evidence_bucket=EVIDENCE_BUCKET,
        web_bucket="fixflow-web-test",
        allowed_origins=("http://localhost:5173",),
        web_url="https://app.example.test",
    )
    return replace(base, **overrides)


def create_tables(client: Any) -> None:
    client.create_table(
        TableName=REPORTS_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "reportId", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
            {"AttributeName": "GSI2PK", "AttributeType": "S"},
            {"AttributeName": "GSI2SK", "AttributeType": "S"},
            {"AttributeName": "GSI3PK", "AttributeType": "S"},
            {"AttributeName": "GSI3SK", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "reportId", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": "GSI1",
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
            {
                "IndexName": "GSI2",
                "KeySchema": [
                    {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
            {
                "IndexName": "GSI3",
                "KeySchema": [
                    {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            },
        ],
    )
    client.create_table(
        TableName=EVENTS_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "reportId", "AttributeType": "S"},
            {"AttributeName": "eventId", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "reportId", "KeyType": "HASH"},
            {"AttributeName": "eventId", "KeyType": "RANGE"},
        ],
    )
    client.create_table(
        TableName=TOKENS_TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": "reportId", "AttributeType": "S"},
            {"AttributeName": "taskType", "AttributeType": "S"},
        ],
        KeySchema=[
            {"AttributeName": "reportId", "KeyType": "HASH"},
            {"AttributeName": "taskType", "KeyType": "RANGE"},
        ],
    )


def sample_report_payload(
    report_id: str | None = None,
    *,
    description: str = (
        "Large pothole in the left lane, about 40 cm wide, causing cars to swerve."
    ),
) -> dict[str, Any]:
    return {
        "reportId": report_id or new_uuid4(),
        "category": "POTHOLE",
        "description": description,
        "severity": "HIGH",
        "location": {
            "area": "Kariakoo",
            "latitude": -6.8189,
            "longitude": 39.2803,
        },
    }


def api_event(
    body: dict[str, Any] | None = None,
    *,
    path_parameters: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    origin: str | None = "http://localhost:5173",
) -> dict[str, Any]:
    headers = {"origin": origin} if origin else {}
    return {
        "version": "2.0",
        "headers": headers,
        "body": json.dumps(body) if body is not None else None,
        "pathParameters": path_parameters or {},
        "queryStringParameters": query,
        "requestContext": {
            "requestId": "test-request-id",
            "http": {"method": "POST", "path": "/"},
        },
    }


def admin_api_event(
    body: dict[str, Any] | None = None,
    *,
    path_parameters: dict[str, str] | None = None,
    query: dict[str, str] | None = None,
    groups: list[str] | str | tuple[str, ...] = ("admin",),
    subject: str = "admin-sub-1",
    origin: str | None = "http://localhost:5173",
) -> dict[str, Any]:
    event = api_event(body, path_parameters=path_parameters, query=query, origin=origin)
    event["requestContext"]["authorizer"] = {
        "jwt": {
            "claims": {
                "sub": subject,
                "cognito:groups": list(groups) if not isinstance(groups, str) else groups,
            }
        }
    }
    return event


class FakeContext:
    aws_request_id = "test-request-id"


class RecordingS3:
    """Minimal S3 fake used to inspect the presigned POST contract."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] | None = None

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, Any]:
        self.kwargs = kwargs
        return {
            "url": "https://example.test/upload",
            "fields": dict(kwargs["Fields"]),
        }


class FakeWorkflow:
    """Records workflow calls without contacting Step Functions."""

    def __init__(self, *, configured: bool = True, fail_start: bool = False) -> None:
        self._configured = configured
        self.fail_start = fail_start
        self.started: list[tuple[str, str]] = []
        self.successes: list[tuple[str, dict[str, Any]]] = []

    @property
    def configured(self) -> bool:
        return self._configured

    def start(self, report_id: str, public_ref: str) -> str:
        if self.fail_start:
            raise RuntimeError("start failed")
        self.started.append((report_id, public_ref))
        return f"arn:aws:states:us-east-1:123456789012:execution:fixflow:{report_id}"

    def send_success(self, task_token: str, output: dict[str, Any]) -> None:
        self.successes.append((task_token, output))


def seed_report(
    repository: Any,
    *,
    status: str = "SUBMITTED",
    version: int = 1,
    report_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    value = validate_report_input(sample_report_payload(report_id))
    built = build_new_report(value, public_ref=new_public_ref(), evidence_key=None)
    built.report["status"] = status
    built.report["version"] = version
    built.report["GSI1PK"] = f"STATUS#{status}"
    built.report.update(overrides)
    repository.create_report(built.report, built.event)
    return built.report
