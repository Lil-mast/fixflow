"""HTTP plumbing shared by every Lambda handler."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from typing import Any

from shared.config import Settings
from shared.errors import (
    AppError,
    BadRequest,
    InternalError,
    PayloadTooLarge,
    ValidationError,
)
from shared.logging import log
from shared.responses import error_response

MAX_JSON_BODY_BYTES = 64 * 1024


def request_id(event: dict[str, Any] | None, context: Any = None) -> str:
    request_context = (event or {}).get("requestContext") or {}
    return getattr(context, "aws_request_id", None) or request_context.get("requestId") or "local"


def _raw_body(event: dict[str, Any]) -> bytes:
    body = event.get("body")
    if body is None:
        raise ValidationError("A JSON request body is required.")
    if not isinstance(body, str):
        raise ValidationError("The request body must be text.")
    try:
        raw = (
            base64.b64decode(body, validate=True)
            if event.get("isBase64Encoded")
            else body.encode("utf-8")
        )
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValidationError("The request body is not valid UTF-8.") from exc
    if len(raw) > MAX_JSON_BODY_BYTES:
        raise PayloadTooLarge("The JSON request body is too large.")
    return raw


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = _raw_body(event)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("The request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValidationError("The request body must be a JSON object.")
    return payload


def path_parameter(event: dict[str, Any], name: str) -> str:
    parameters = event.get("pathParameters") or {}
    value = parameters.get(name)
    if not isinstance(value, str) or not value:
        raise BadRequest(f"Missing path parameter: {name}.")
    return value


def handle_api(
    event: dict[str, Any],
    context: Any,
    settings: Settings,
    operation: Callable[[], dict[str, Any]],
    *,
    operation_name: str,
) -> dict[str, Any]:
    """Run an operation and convert failures into the documented error envelope."""

    request = request_id(event, context)
    try:
        return operation()
    except AppError as error:
        log(
            "info" if error.status_code < 500 else "error",
            "request rejected",
            operation=operation_name,
            requestId=request,
            errorCode=error.code,
            status=error.status_code,
        )
        return error_response(
            error,
            request_id=request,
            event=event,
            allowed_origins=settings.allowed_origins,
        )
    except Exception as exc:  # noqa: BLE001 - the boundary must not leak internals
        log(
            "error",
            "unhandled error",
            operation=operation_name,
            requestId=request,
            errorType=type(exc).__name__,
        )
        return error_response(
            InternalError(),
            request_id=request,
            event=event,
            allowed_origins=settings.allowed_origins,
        )
