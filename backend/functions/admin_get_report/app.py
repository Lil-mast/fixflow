"""``GET /admin/reports/{reportId}`` - full report, audit history, evidence URLs."""

from __future__ import annotations

from typing import Any

from shared.auth import admin_identity
from shared.clock import seconds_from_now, to_iso
from shared.config import load_settings
from shared.domain import admin_report_projection
from shared.errors import NotFound
from shared.http import handle_api, path_parameter
from shared.repository import ReportRepository
from shared.responses import json_response
from shared.storage import EvidenceStorage

MAX_EVENTS = 500


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    repository: ReportRepository | None = None,
    storage: EvidenceStorage | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        admin_identity(event)
        report_id = path_parameter(event, "reportId")

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        report = repo.get_report(report_id)
        if report is None:
            raise NotFound("No report matches that identifier.")

        events: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(events) < MAX_EVENTS:
            page, cursor = repo.list_events(report_id, limit=100, cursor=cursor)
            events.extend(page)
            if not cursor:
                break

        store = storage
        if store is None and (report.get("evidenceKey") or report.get("resolutionEvidenceKey")):
            if runtime.evidence_bucket:
                store = EvidenceStorage(runtime.evidence_bucket, region=runtime.region)

        evidence_url = None
        resolution_url = None
        if store is not None:
            evidence_url = store.presign_download(
                report.get("evidenceKey"),
                expires_in=runtime.evidence_url_expiry_seconds,
            )
            resolution_url = store.presign_download(
                report.get("resolutionEvidenceKey"),
                expires_in=runtime.evidence_url_expiry_seconds,
            )

        return json_response(
            200,
            admin_report_projection(
                report,
                events,
                evidence_url=evidence_url,
                resolution_url=resolution_url,
                evidence_expires_at=to_iso(seconds_from_now(runtime.evidence_url_expiry_seconds)),
            ),
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="admin_get_report",
    )
