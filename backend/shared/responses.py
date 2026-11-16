"""JSON responses, CORS, and the shared error envelope.

The API never reflects an arbitrary ``Origin``. Only exact entries from the configured
allowlist receive an ``Access-Control-Allow-Origin`` header.
"""

from __future__ import annotations

import json
from typing import Any

from shared.errors import AppError

JSON_CONTENT_TYPE = "application/json; charset=utf-8"

SECURITY_HEADERS = {
    "content-type": JSON_CONTENT_TYPE,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
}


def request_origin(event: dict[str, Any] | None) -> str | None:
    """Return the request ``Origin`` header, case-insensitively."""

    headers = (event or {}).get("headers") or {}
    for key, value in headers.items():
        if key.lower() == "origin" and isinstance(value, str) and value:
            return value.rstrip("/")
    return None


def cors_headers(
    origin: str | None, allowed_origins: tuple[str, ...] | list[str]
) -> dict[str, str]:
    if not origin or origin not in allowed_origins:
        return {}
    return {
        "access-control-allow-origin": origin,
        "access-control-allow-credentials": "false",
        "vary": "Origin",
    }


def json_response(
    status_code: int,
    body: dict[str, Any],
    *,
    event: dict[str, Any] | None = None,
    allowed_origins: tuple[str, ...] | list[str] = (),
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = dict(SECURITY_HEADERS)
    headers.update(cors_headers(request_origin(event), allowed_origins))
    if extra_headers:
        headers.update(extra_headers)
    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body, separators=(",", ":"), ensure_ascii=False),
    }


def error_response(
    error: AppError,
    *,
    request_id: str,
    event: dict[str, Any] | None = None,
    allowed_origins: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
            "requestId": request_id,
        }
    }
    if error.details:
        payload["error"]["details"] = error.details
    return json_response(
        error.status_code,
        payload,
        event=event,
        allowed_origins=allowed_origins,
    )
