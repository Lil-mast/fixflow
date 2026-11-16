"""Step Functions start and callback helpers."""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.errors import ServiceUnavailable, TaskExpired


class WorkflowClient:
    def __init__(
        self,
        state_machine_arn: str = "",
        *,
        client: Any = None,
        region: str = "us-east-1",
    ) -> None:
        self.state_machine_arn = state_machine_arn
        self.client = client or (
            boto3.client("stepfunctions", region_name=region) if state_machine_arn else None
        )

    @property
    def configured(self) -> bool:
        return bool(self.state_machine_arn and self.client)

    def start(self, report_id: str, public_ref: str) -> str | None:
        """Start the report workflow. ``ExecutionAlreadyExists`` is success."""

        if not self.configured:
            return None
        try:
            response = self.client.start_execution(
                stateMachineArn=self.state_machine_arn,
                name=report_id,
                input=json.dumps(
                    {"reportId": report_id, "publicRef": public_ref},
                    separators=(",", ":"),
                ),
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
                return None
            raise
        return response.get("executionArn")

    def send_success(self, task_token: str, output: dict[str, Any]) -> None:
        try:
            self.client.send_task_success(
                taskToken=task_token,
                output=json.dumps(output, separators=(",", ":")),
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"InvalidToken", "TaskTimedOut"}:
                raise TaskExpired() from exc
            raise ServiceUnavailable("The workflow could not be resumed.") from exc
