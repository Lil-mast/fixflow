from __future__ import annotations

import json
import re
from datetime import datetime

from functions.health.app import handler


class FakeContext:
    aws_request_id = "test-request-id"


def api_event(origin: str | None = None) -> dict:
    headers = {"origin": origin} if origin else {}
    return {
        "version": "2.0",
        "routeKey": "GET /health",
        "rawPath": "/health",
        "headers": headers,
        "requestContext": {
            "requestId": "api-request-id",
            "http": {"method": "GET", "path": "/health"},
        },
    }


def test_health_returns_documented_body() -> None:
    response = handler(api_event(), FakeContext())

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["status"] == "ok"
    assert body["service"] == "fixflow"
    assert set(body) == {"status", "service", "time"}

    parsed = datetime.strptime(body["time"], "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.tzinfo is None


def test_health_sets_security_headers() -> None:
    response = handler(api_event(), FakeContext())
    headers = response["headers"]

    assert headers["content-type"] == "application/json; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert headers["x-request-id"] == "test-request-id"


def test_health_reflects_only_allowed_origin() -> None:
    response = handler(api_event("http://localhost:5173"), FakeContext())
    assert response["headers"]["access-control-allow-origin"] == "http://localhost:5173"
    assert response["headers"]["vary"] == "Origin"


def test_health_does_not_reflect_unknown_origin() -> None:
    response = handler(api_event("https://evil.example"), FakeContext())
    assert "access-control-allow-origin" not in response["headers"]


def test_health_time_is_iso_utc() -> None:
    response = handler(api_event(), FakeContext())
    body = json.loads(response["body"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", body["time"])
