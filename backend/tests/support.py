"""Shared fixtures and builders for backend tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from shared.config import Settings
from shared.ids import new_uuid4

REPORTS_TABLE = "FixFlowReports"
EVENTS_TABLE = "FixFlowReportEvents"
EVIDENCE_BUCKET = "fixflow-evidence-test"


def make_settings(**overrides: Any) -> Settings:
    base = Settings(
        stage="test",
        region="us-east-1",
        reports_table=REPORTS_TABLE,
        events_table=EVENTS_TABLE,
        tokens_table="FixFlowWorkflowTokens",
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
