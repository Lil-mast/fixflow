"""Workflow step: persist a task token before waiting.

The token is sensitive. It is written to DynamoDB and never logged or returned to a
caller. The task completes only when an administrator callback calls ``SendTaskSuccess``.
"""

from __future__ import annotations

from typing import Any

from shared.clock import epoch_seconds, now_iso
from shared.config import load_settings
from shared.logging import log
from shared.repository import WorkflowTokenRepository

TOKEN_TTL_GRACE_SECONDS = 24 * 60 * 60


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    tokens: WorkflowTokenRepository | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()
    report_id = event.get("reportId")
    task_type = event.get("taskType")
    task_token = event.get("taskToken")
    execution_arn = event.get("executionArn")
    for field, value in (
        ("reportId", report_id),
        ("taskType", task_type),
        ("taskToken", task_token),
        ("executionArn", execution_arn),
    ):
        if not isinstance(value, str) or not value:
            raise ValueError(f"{field} is required")

    timeout_seconds = int(event.get("timeoutSeconds") or runtime.review_timeout_seconds)
    expires_at = epoch_seconds() + timeout_seconds + TOKEN_TTL_GRACE_SECONDS

    store = tokens or WorkflowTokenRepository(runtime.tokens_table, region=runtime.region)
    stored = store.store_token(
        report_id=report_id,
        task_type=task_type,
        task_token=task_token,
        execution_arn=execution_arn,
        expires_at=expires_at,
        now=now_iso(),
    )
    log(
        "info",
        "workflow task token stored",
        reportId=report_id,
        taskType=task_type,
        taskVersion=stored.get("taskVersion"),
    )
    return {
        "reportId": report_id,
        "taskType": task_type,
        "taskVersion": int(stored.get("taskVersion", 0)),
        "expiresAt": expires_at,
    }
