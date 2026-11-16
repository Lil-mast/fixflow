"""DynamoDB persistence for reports, events, and workflow task tokens."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from shared.clock import epoch_seconds, now_iso
from shared.pagination import decode_cursor, encode_cursor

TOKEN_PENDING = "PENDING"
TOKEN_CONSUMED = "CONSUMED"


class DuplicateReport(Exception):
    """Raised when a conditional create loses the idempotency race."""


class ReportNotFound(Exception):
    """Raised when a transition targets a report that does not exist."""


class VersionConflict(Exception):
    """Raised when a conditional update loses the optimistic-concurrency race."""


class InvalidStatus(Exception):
    """Raised when the current status is not a legal source for the transition."""


class ReviewTokenUnavailable(Exception):
    """Raised when the review token is missing, consumed, or stale."""


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


def _cancelled(reasons: list[dict[str, Any]], index: int) -> bool:
    return len(reasons) > index and (reasons[index] or {}).get("Code") == "ConditionalCheckFailed"


def _update_parts(
    fields: dict[str, Any],
    remove_fields: Sequence[str],
    *,
    prefix: str,
) -> tuple[list[str], list[str], dict[str, str], dict[str, Any]]:
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    set_parts: list[str] = []
    for index, (key, value) in enumerate(fields.items()):
        name_key = f"#{prefix}n{index}"
        value_key = f":{prefix}v{index}"
        names[name_key] = key
        values[value_key] = _serialize({key: value})[key]
        set_parts.append(f"{name_key} = {value_key}")
    remove_parts: list[str] = []
    for index, key in enumerate(remove_fields):
        name_key = f"#{prefix}r{index}"
        names[name_key] = key
        remove_parts.append(name_key)
    return set_parts, remove_parts, names, values


class ReportRepository:
    def __init__(
        self,
        reports_table: str,
        events_table: str,
        *,
        tokens_table: str = "",
        client: Any = None,
        region: str = "us-east-1",
    ) -> None:
        self.reports_table = reports_table
        self.events_table = events_table
        self.tokens_table = tokens_table
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

    def transition_report(
        self,
        *,
        report_id: str,
        expected_statuses: Sequence[str],
        target_status: str,
        fields: dict[str, Any],
        event: dict[str, Any],
        expected_version: int | None = None,
        remove_fields: Sequence[str] = (),
        allow_same_status: bool = False,
        ignore_statuses: Sequence[str] = (),
        consume_token: tuple[str, int] | None = None,
        consumed_by: str = "SYSTEM",
        now: str | None = None,
    ) -> dict[str, Any]:
        """Move a report to ``target_status`` and append its event atomically.

        The report update, event put, and optional token consumption share one
        transaction. A token cancellation is reported separately so the caller can return
        ``REVIEW_EXPIRED`` instead of a generic conflict.
        """

        current = self.get_report(report_id)
        if current is None:
            raise ReportNotFound(report_id)
        if current["status"] in ignore_statuses:
            return current

        current_version = int(current["version"])
        if expected_version is not None and current_version != expected_version:
            raise VersionConflict(f"Report version is {current_version}, not {expected_version}.")
        if current["status"] == target_status and not allow_same_status:
            return current
        if current["status"] not in expected_statuses:
            raise InvalidStatus(f"Cannot move {current['status']} to {target_status}.")

        timestamp = now or now_iso()
        public_summary = None
        if event.get("visibility") == "PUBLIC" and event.get("publicMessage"):
            public_summary = event["publicMessage"]

        unsafe = {"reportId", "status", "version", "updatedAt", "GSI1PK", "GSI1SK"}
        if unsafe & set(fields):
            raise ValueError(f"Transition fields must not include {sorted(unsafe & set(fields))}")

        update_fields: dict[str, Any] = {
            "status": target_status,
            "version": current_version + 1,
            "updatedAt": timestamp,
            "GSI1PK": f"STATUS#{target_status}",
        }
        update_fields.update({key: value for key, value in fields.items() if value is not None})
        if public_summary:
            update_fields["publicSummary"] = public_summary

        removable = [key for key in remove_fields if key not in update_fields]
        set_parts, remove_parts, names, values = _update_parts(update_fields, removable, prefix="s")
        names["#status"] = "status"
        names["#currentVersion"] = "version"
        values[":currentVersion"] = {"N": str(current_version)}
        expected_names = []
        for index, status in enumerate(dict.fromkeys(expected_statuses)):
            key = f":expected{index}"
            expected_names.append(key)
            values[key] = {"S": status}
        condition = (
            f"#status IN ({', '.join(expected_names)}) AND #currentVersion = :currentVersion"
        )
        expression = "SET " + ", ".join(set_parts)
        if remove_parts:
            expression += " REMOVE " + ", ".join(remove_parts)

        items: list[dict[str, Any]] = [
            {
                "Update": {
                    "TableName": self.reports_table,
                    "Key": {"reportId": {"S": report_id}},
                    "UpdateExpression": expression,
                    "ConditionExpression": condition,
                    "ExpressionAttributeNames": names,
                    "ExpressionAttributeValues": values,
                }
            },
            {
                "Put": {
                    "TableName": self.events_table,
                    "Item": _serialize(event),
                }
            },
        ]

        if consume_token is not None:
            if not self.tokens_table:
                raise ValueError("A tokens table is required to consume a task token")
            task_type, task_version = consume_token
            items.append(
                {
                    "Update": {
                        "TableName": self.tokens_table,
                        "Key": {
                            "reportId": {"S": report_id},
                            "taskType": {"S": task_type},
                        },
                        "UpdateExpression": (
                            "SET #tokenStatus = :consumed, consumedAt = :consumedAt, "
                            "consumedBy = :consumedBy"
                        ),
                        "ConditionExpression": (
                            "#tokenStatus = :pending AND taskVersion = :taskVersion"
                        ),
                        "ExpressionAttributeNames": {"#tokenStatus": "status"},
                        "ExpressionAttributeValues": {
                            ":consumed": {"S": TOKEN_CONSUMED},
                            ":pending": {"S": TOKEN_PENDING},
                            ":taskVersion": {"N": str(task_version)},
                            ":consumedAt": {"S": timestamp},
                            ":consumedBy": {"S": consumed_by},
                        },
                    }
                }
            )

        try:
            self.client.transact_write_items(TransactItems=items)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            reasons = exc.response.get("CancellationReasons") or []
            if consume_token is not None and _cancelled(reasons, 2):
                raise ReviewTokenUnavailable(report_id) from exc

            latest = self.get_report(report_id)
            if latest is None:
                raise ReportNotFound(report_id) from exc
            if latest["status"] in ignore_statuses:
                return latest
            if latest["status"] == target_status and not allow_same_status:
                return latest
            if expected_version is not None and int(latest["version"]) != expected_version:
                raise VersionConflict(
                    f"Report version is {latest['version']}, not {expected_version}."
                ) from exc
            raise InvalidStatus(f"Cannot move {latest['status']} to {target_status}.") from exc

        return self.get_report(report_id) or current

    def set_priority(
        self,
        *,
        report_id: str,
        score: int,
        priority: str,
        event: dict[str, Any],
        now: str | None = None,
    ) -> dict[str, Any]:
        """Store the deterministic priority and an operational event.

        Re-running this step is a no-op, so a Step Functions retry cannot duplicate the
        event. Priority is not a lifecycle transition, so the version is unchanged.
        """

        current = self.get_report(report_id)
        if current is None:
            raise ReportNotFound(report_id)
        if current.get("priorityScore") == score and current.get("priority") == priority:
            return current

        timestamp = now or now_iso()
        condition = "#version = :version"
        names = {"#version": "version", "#score": "priorityScore", "#priority": "priority"}
        values = {
            ":version": {"N": str(int(current["version"]))},
            ":score": {"N": str(score)},
            ":priority": {"S": priority},
            ":updatedAt": {"S": timestamp},
        }
        names["#updatedAt"] = "updatedAt"

        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.reports_table,
                            "Key": {"reportId": {"S": report_id}},
                            "UpdateExpression": (
                                "SET #score = :score, #priority = :priority, "
                                "#updatedAt = :updatedAt"
                            ),
                            "ConditionExpression": condition,
                            "ExpressionAttributeNames": names,
                            "ExpressionAttributeValues": values,
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
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            latest = self.get_report(report_id)
            if latest is None:
                raise ReportNotFound(report_id) from exc
            if latest.get("priorityScore") == score and latest.get("priority") == priority:
                return latest
            raise VersionConflict("The report changed while priority was calculated.") from exc

        return self.get_report(report_id) or current

    def mark_workflow_started(self, report_id: str, execution_arn: str | None = None) -> None:
        fields = {"workflowStatus": "STARTED", "updatedAt": now_iso()}
        if execution_arn:
            fields["workflowExecutionArn"] = execution_arn
        set_parts, _, names, values = _update_parts(fields, (), prefix="w")
        try:
            self.client.update_item(
                TableName=self.reports_table,
                Key={"reportId": {"S": report_id}},
                UpdateExpression="SET " + ", ".join(set_parts),
                ConditionExpression="attribute_exists(reportId)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise ReportNotFound(report_id) from exc
            raise

    def record_workflow_failure(
        self,
        *,
        report_id: str,
        event: dict[str, Any],
        execution_arn: str | None = None,
        now: str | None = None,
    ) -> None:
        """Set ``workflowStatus = FAILED`` without changing the lifecycle status."""

        current = self.get_report(report_id)
        if current is None:
            raise ReportNotFound(report_id)

        fields: dict[str, Any] = {
            "workflowStatus": "FAILED",
            "updatedAt": now or now_iso(),
        }
        if execution_arn:
            fields["workflowExecutionArn"] = execution_arn

        set_parts, _, names, values = _update_parts(fields, (), prefix="f")
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.reports_table,
                            "Key": {"reportId": {"S": report_id}},
                            "UpdateExpression": "SET " + ", ".join(set_parts),
                            "ConditionExpression": "attribute_exists(reportId)",
                            "ExpressionAttributeNames": names,
                            "ExpressionAttributeValues": values,
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
            if exc.response.get("Error", {}).get("Code") != "TransactionCanceledException":
                raise
            raise ReportNotFound(report_id) from exc

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


class WorkflowTokenRepository:
    """Task tokens, keyed by report and task type.

    A new token increments ``taskVersion`` so a late callback from an older token cannot
    consume the replacement token issued after a timeout.
    """

    def __init__(
        self,
        tokens_table: str,
        *,
        client: Any = None,
        region: str = "us-east-1",
    ) -> None:
        self.tokens_table = tokens_table
        self.client = client or boto3.client("dynamodb", region_name=region)

    def store_token(
        self,
        *,
        report_id: str,
        task_type: str,
        task_token: str,
        execution_arn: str,
        expires_at: int,
        now: str | None = None,
    ) -> dict[str, Any]:
        timestamp = now or now_iso()
        response = self.client.update_item(
            TableName=self.tokens_table,
            Key={
                "reportId": {"S": report_id},
                "taskType": {"S": task_type},
            },
            UpdateExpression=(
                "SET taskToken = :taskToken, executionArn = :executionArn, "
                "#status = :pending, createdAt = :createdAt, expiresAt = :expiresAt, "
                "taskVersion = if_not_exists(taskVersion, :zero) + :one "
                "REMOVE consumedAt, consumedBy"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":taskToken": {"S": task_token},
                ":executionArn": {"S": execution_arn},
                ":pending": {"S": TOKEN_PENDING},
                ":createdAt": {"S": timestamp},
                ":expiresAt": {"N": str(expires_at)},
                ":zero": {"N": "0"},
                ":one": {"N": "1"},
            },
            ReturnValues="ALL_NEW",
        )
        return _deserialize(response.get("Attributes") or {})

    def get_pending_token(
        self, report_id: str, task_type: str, *, now: int | None = None
    ) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.tokens_table,
            Key={
                "reportId": {"S": report_id},
                "taskType": {"S": task_type},
            },
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        token = _deserialize(item)
        if token.get("status") != TOKEN_PENDING:
            return None
        if int(token.get("expiresAt", 0)) <= (now if now is not None else epoch_seconds()):
            return None
        return token
