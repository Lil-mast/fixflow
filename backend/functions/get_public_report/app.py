"""``GET /public/reports/{publicRef}`` - filtered public projection."""

from __future__ import annotations

from typing import Any

from shared.config import load_settings
from shared.domain import public_report_projection
from shared.errors import NotFound
from shared.http import handle_api, path_parameter
from shared.ids import normalize_public_ref
from shared.repository import ReportRepository
from shared.responses import json_response


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        public_ref = normalize_public_ref(path_parameter(event, "publicRef"))
        if public_ref is None:
            raise NotFound("No report matches that reference.")

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        report = repo.find_by_public_ref(public_ref)
        if report is None:
            raise NotFound("No report matches that reference.")

        return json_response(
            200,
            public_report_projection(report),
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="get_public_report",
    )
