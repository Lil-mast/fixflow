"""Structured JSON logging with a redaction list.

CloudWatch logs are queryable, but they are also a data store with a different access
model. Log only the allowlisted operational fields. Anything that looks like a secret,
a presigned URL, a token, or resident PII is replaced before it reaches stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any

REDACTED = "[REDACTED]"

# Exact keys from the security doc's forbidden list.
_FORBIDDEN_KEYS = frozenset(
    {
        "name",
        "email",
        "phone",
        "description",
        "latitude",
        "longitude",
        "body",
        "rawbody",
        "headers",
        "presignedurl",
        "presigned_url",
        "policy",
        "signature",
        "evidencekey",
        "resolutionevidencekey",
    }
)

# Substrings that make any key sensitive, including future fields.
_SENSITIVE_FRAGMENTS = (
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "credential",
    "signature",
    "apikey",
    "api_key",
)


def _is_sensitive(key: str) -> bool:
    normalized = key.replace("-", "").replace("_", "").lower()
    if key.lower() in _FORBIDDEN_KEYS or normalized in _FORBIDDEN_KEYS:
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_FRAGMENTS)


def redact(value: Any) -> Any:
    """Return a copy of ``value`` with sensitive keys and nested values removed."""

    if isinstance(value, dict):
        return {
            key: REDACTED if _is_sensitive(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


def log(level: str, message: str, **fields: Any) -> None:
    """Emit one JSON line. Never pass a raw event, request body, or AWS error object."""

    payload: dict[str, Any] = {
        "level": level.upper(),
        "service": "fixflow",
        "message": message,
    }
    payload.update(redact(fields))
    print(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str),
        file=sys.stdout,
        flush=True,
    )
