"""``GET /admin/metrics`` - operational counts and averages."""

from __future__ import annotations

from typing import Any

from shared.auth import admin_identity
from shared.config import load_settings
from shared.http import handle_api
from shared.metrics import compute_metrics
from shared.repository import ReportRepository
from shared.responses import json_response
from shared.validation import validate_metrics_query


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        admin_identity(event)
        include_demo = validate_metrics_query(event.get("queryStringParameters"))

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        reports = repo.all_reports(include_demo=include_demo)
        return json_response(
            200,
            compute_metrics(reports, include_demo=include_demo),
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="admin_metrics",
    )
