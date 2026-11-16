"""Cognito claim checks for admin routes.

API Gateway's JWT authorizer rejects missing or invalid tokens before the Lambda runs.
The handler still checks the ``admin`` group: authorization is a server-side decision, not
a property of the route configuration alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shared.errors import Forbidden, Unauthorized

ADMIN_GROUP = "admin"


@dataclass(frozen=True)
class AdminIdentity:
    subject: str
    groups: tuple[str, ...]


def _claims(event: dict[str, Any]) -> dict[str, Any]:
    context = (event.get("requestContext") or {}).get("authorizer") or {}
    jwt = context.get("jwt")
    if isinstance(jwt, dict):
        claims = jwt.get("claims")
        if isinstance(claims, dict):
            return claims
        return jwt
    return {}


def _groups(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(group.strip() for group in value.split(",") if group.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(group) for group in value if group)
    return ()


def admin_identity(event: dict[str, Any]) -> AdminIdentity:
    claims = _claims(event)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise Unauthorized()

    groups = _groups(claims.get("cognito:groups"))
    if ADMIN_GROUP not in groups:
        raise Forbidden("Administrator access is required.")
    return AdminIdentity(subject=subject, groups=groups)
