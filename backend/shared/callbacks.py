"""Shared helpers for administrator callbacks that resume a waiting workflow task."""

from __future__ import annotations

from typing import Any

from shared.errors import Conflict, InvalidStateTransition, NotFound, TaskExpired
from shared.logging import log
from shared.repository import (
    InvalidStatus,
    ReportNotFound,
    TaskTokenUnavailable,
    VersionConflict,
)


def apply_callback_transition(repository: Any, **kwargs: Any) -> dict[str, Any]:
    """Translate repository failures into the documented API errors."""

    try:
        return repository.transition_report(**kwargs)
    except ReportNotFound as exc:
        raise NotFound("No report matches that identifier.") from exc
    except VersionConflict as exc:
        raise Conflict("The report changed since it was loaded. Refetch and retry.") from exc
    except InvalidStatus as exc:
        raise InvalidStateTransition(str(exc)) from exc
    except TaskTokenUnavailable as exc:
        raise TaskExpired() from exc


def send_callback_success(
    flow: Any, task_token: str, output: dict[str, Any], *, report_id: str
) -> None:
    try:
        flow.send_success(task_token, output)
    except TaskExpired:
        log(
            "error",
            "callback recorded but workflow token was no longer valid",
            reportId=report_id,
        )
        raise
