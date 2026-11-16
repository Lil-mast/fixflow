"""``POST /reports/upload-url`` - create a short-lived presigned POST."""

from __future__ import annotations

from typing import Any

from shared.config import load_settings
from shared.errors import InternalError
from shared.http import handle_api, parse_json_body
from shared.responses import json_response
from shared.storage import EvidenceStorage
from shared.validation import validate_upload_request


def handler(
    event: dict[str, Any],
    context: Any = None,
    *,
    settings: Any = None,
    storage: EvidenceStorage | None = None,
) -> dict[str, Any]:
    runtime = settings or load_settings()

    def operation() -> dict[str, Any]:
        request = validate_upload_request(parse_json_body(event))
        store = storage
        if store is None:
            if not runtime.evidence_bucket:
                raise InternalError("Evidence storage is not configured.")
            store = EvidenceStorage(runtime.evidence_bucket, region=runtime.region)

        signed = store.create_upload(
            request,
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
        operation_name="create_upload_url",
    )
