"""``POST /admin/reports/{reportId}/resolution/upload-url``.

Create a presigned POST for completion evidence. Only an ``IN_PROGRESS`` report accepts
one. The object lands under ``incoming/resolution/<reportId>/`` and is copied to the
private ``resolution/`` prefix only when the administrator submits it.
"""

from __future__ import annotations

from typing import Any

from shared.auth import admin_identity
from shared.config import load_settings
from shared.errors import InternalError, InvalidStateTransition, NotFound
from shared.http import handle_api, parse_json_body, path_parameter
from shared.repository import ReportRepository
from shared.responses import json_response
from shared.storage import EvidenceStorage
from shared.validation import validate_resolution_upload_request


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
        request = validate_resolution_upload_request(parse_json_body(event), report_id)

        repo = repository or ReportRepository(
            runtime.reports_table,
            runtime.events_table,
            region=runtime.region,
        )
        report = repo.get_report(report_id)
        if report is None:
            raise NotFound("No report matches that identifier.")
        if report["status"] != "IN_PROGRESS":
            raise InvalidStateTransition(
                "Resolution evidence is only accepted while the report is IN_PROGRESS.",
                {"status": report["status"]},
            )

        store = storage
        if store is None:
            if not runtime.evidence_bucket:
                raise InternalError("Evidence storage is not configured.")
            store = EvidenceStorage(runtime.evidence_bucket, region=runtime.region)

        signed = store.create_upload(
            request,
            purpose="resolution",
            expires_in=runtime.upload_expiry_seconds,
        )
        return json_response(
            200,
            {
                "upload": signed.upload,
                "objectKey": signed.object_key,
                "uploadId": signed.upload_id,
                "expiresAt": signed.expires_at,
            },
            event=event,
            allowed_origins=runtime.allowed_origins,
        )

    return handle_api(
        event,
        context,
        runtime,
        operation,
        operation_name="create_resolution_upload_url",
    )
