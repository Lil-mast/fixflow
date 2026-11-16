"""DynamoDB persistence for reports and their append-only event history."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from shared.pagination import decode_cursor, encode_cursor


class DuplicateReport(Exception):
    """Raised when a conditional create loses the idempotency race."""


def _make_dynamo_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: _make_dynamo_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_make_dynamo_safe(item) for item in value]
    return value


def _serialize(item: dict[str, Any]) -> dict[str, Any]:
    serializer = TypeSerializer()
    return {
        key: serializer.serialize(_make_dynamo_safe(value))
        for key, value in item.items()
        if value is not None
    }


def _deserialize(item: dict[str, Any]) -> dict[str, Any]:
    deserializer = TypeDeserializer()
    return {key: deserializer.deserialize(value) for key, value in item.items()}


class ReportRepository:
    def __init__(
        self,
        reports_table: str,
        events_table: str,
        *,
        client: Any = None,
        region: str = "us-east-1",
    ) -> None:
        self.reports_table = reports_table
        self.events_table = events_table
        self.client = client or boto3.client("dynamodb", region_name=region)

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.reports_table,
            Key={"reportId": {"S": report_id}},
            ConsistentRead=True,
        )
        item = response.get("Item")
        return _deserialize(item) if item else None

    def find_by_public_ref(self, public_ref: str) -> dict[str, Any] | None:
        response = self.client.query(
            TableName=self.reports_table,
            IndexName="GSI2",
            KeyConditionExpression="GSI2PK = :pk AND GSI2SK = :sk",
            ExpressionAttributeValues={
                ":pk": {"S": f"PUBLIC#{public_ref}"},
                ":sk": {"S": "REPORT"},
            },
            Limit=1,
        )
        items = response.get("Items") or []
        if not items:
            return None
        report_id = items[0].get("reportId", {}).get("S")
        return self.get_report(report_id) if report_id else None

    def create_report(self, report: dict[str, Any], event: dict[str, Any]) -> None:
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self.reports_table,
                            "Item": _serialize(report),
                            "ConditionExpression": "attribute_not_exists(reportId)",
                        }
                    },
                    {
                        "Put": {
                            "TableName": self.events_table,
                            "Item": _serialize(event),
                        }
                    },
                ]
            )
        except ClientError as exc:
            error = exc.response.get("Error", {})
            reasons = exc.response.get("CancellationReasons") or []
            if error.get("Code") == "TransactionCanceledException" and any(
                reason.get("Code") == "ConditionalCheckFailed" for reason in reasons
            ):
                raise DuplicateReport from exc
            raise

    def list_public_events(
        self,
        report_id: str,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        request: dict[str, Any] = {
            "TableName": self.events_table,
            "KeyConditionExpression": "reportId = :report_id",
            "FilterExpression": ("#visibility = :visibility AND attribute_exists(publicMessage)"),
            "ExpressionAttributeNames": {"#visibility": "visibility"},
            "ExpressionAttributeValues": {
                ":report_id": {"S": report_id},
                ":visibility": {"S": "PUBLIC"},
            },
            "ScanIndexForward": True,
            "Limit": limit,
        }
        if cursor:
            request["ExclusiveStartKey"] = decode_cursor(cursor)

        response = self.client.query(**request)
        items = [_deserialize(item) for item in response.get("Items", [])]
        return items, encode_cursor(response.get("LastEvaluatedKey"))
