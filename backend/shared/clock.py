"""Time helpers.

DynamoDB timestamps are ISO-8601 UTC strings with millisecond precision. They sort
lexicographically, which is why the GSI sort key is ``<createdAt>#<reportId>``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(value: datetime | None = None) -> str:
    moment = value or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return to_iso()


def seconds_from_now(seconds: int) -> datetime:
    return utc_now() + timedelta(seconds=seconds)


def epoch_seconds(value: datetime | None = None) -> int:
    return int((value or utc_now()).timestamp())
