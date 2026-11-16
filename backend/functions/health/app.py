"""``GET /health`` - shallow liveness check.

Deliberately does not call DynamoDB, S3, or Step Functions. A deep readiness check adds
cost and coupling without improving the demo, and it would make a dependency outage look
like a broken deployment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from shared.config import load_settings
from shared.http import request_id
from shared.responses import json_response


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    settings = load_settings()
    body = {
        "status": "ok",
        "service": "fixflow",
        "time": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return json_response(
        200,
        body,
        event=event,
        allowed_origins=settings.allowed_origins,
        extra_headers={"x-request-id": request_id(event, context)},
    )
