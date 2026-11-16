"""``GET /admin/reports`` - paginated report list for the dashboard."""

from __future__ import annotations

from typing import Any

from shared.auth import admin_identity
from shared.config import load_settings
from shared.domain import admin_list_projection
from shared.http import handle_api
from shared.repository import ReportRepository
from shared.responses import json_response
from shared.validation import validate_admin_list_query


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
        query = validate_admin_list_query(event.get("queryStringParameters"))

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        reports, next_cursor = repo.list_reports(
            status=query.status,
            category=query.category,
            limit=query.limit,
            cursor=query.cursor,
            include_demo=query.include_demo,
        )
        return json_response(
            200,
            {
                "items": [admin_list_projection(report) for report in reports],
                "nextCursor": next_cursor,
            },
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="admin_list_reports",
    )
