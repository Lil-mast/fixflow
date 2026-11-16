"""Opaque cursor helpers for DynamoDB pagination."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from shared.errors import ValidationError


def encode_cursor(last_evaluated_key: dict[str, Any] | None) -> str | None:
    if not last_evaluated_key:
        return None
    payload = json.dumps(
        last_evaluated_key, separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        value = json.loads(raw)
    except (ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValidationError("Invalid cursor.", {"field": "cursor"}) from exc
    if not isinstance(value, dict) or not value:
        raise ValidationError("Invalid cursor.", {"field": "cursor"})
    return value
