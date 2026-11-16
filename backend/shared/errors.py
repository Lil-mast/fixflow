"""Application errors with a stable machine-readable code.

Handlers raise these instead of returning ad-hoc error bodies. The response mapper keeps
the public message generic and never includes stack traces, table names, or AWS errors.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class BadRequest(AppError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("BAD_REQUEST", message, 400, details)


class ValidationError(AppError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("VALIDATION_ERROR", message, 400, details)


class Unauthorized(AppError):
    def __init__(self, message: str = "Authentication is required.") -> None:
        super().__init__("UNAUTHORIZED", message, 401)


class Forbidden(AppError):
    def __init__(self, message: str = "You do not have permission to do that.") -> None:
        super().__init__("FORBIDDEN", message, 403)


class NotFound(AppError):
    def __init__(self, message: str = "The requested resource was not found.") -> None:
        super().__init__("NOT_FOUND", message, 404)


class Conflict(AppError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("CONFLICT", message, 409, details)


class InvalidStateTransition(AppError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("INVALID_STATE_TRANSITION", message, 409, details)


class ReviewExpired(AppError):
    def __init__(
        self, message: str = "The review window has expired. Refetch the report and retry."
    ) -> None:
        super().__init__("REVIEW_EXPIRED", message, 409)


class TaskExpired(AppError):
    def __init__(
        self,
        message: str = "The workflow task has expired. Refetch the report and retry.",
    ) -> None:
        super().__init__("TASK_EXPIRED", message, 409)


class RateLimited(AppError):
    def __init__(self, message: str = "Too many requests. Try again shortly.") -> None:
        super().__init__("RATE_LIMITED", message, 429)


class PayloadTooLarge(AppError):
    def __init__(self, message: str = "The request payload is too large.") -> None:
        super().__init__("PAYLOAD_TOO_LARGE", message, 413)


class UnsupportedMediaType(AppError):
    def __init__(self, message: str) -> None:
        super().__init__("UNSUPPORTED_MEDIA_TYPE", message, 415)


class UnprocessableEntity(AppError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__("UNPROCESSABLE_ENTITY", message, 422, details)


class ServiceUnavailable(AppError):
    def __init__(self, message: str = "The service is temporarily unavailable.") -> None:
        super().__init__("SERVICE_UNAVAILABLE", message, 503)


class InternalError(AppError):
    def __init__(self, message: str = "An unexpected error occurred.") -> None:
        super().__init__("INTERNAL_ERROR", message, 500)
