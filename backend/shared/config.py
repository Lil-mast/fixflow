"""Runtime configuration read from environment variables.

Every value has a local-development default so handlers can be unit tested without a
deployed stack. Production values are injected by the SAM template.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_ALLOWED_ORIGINS = "http://localhost:5173"


@dataclass(frozen=True)
class Settings:
    stage: str
    region: str
    reports_table: str
    events_table: str
    tokens_table: str
    evidence_bucket: str
    web_bucket: str
    allowed_origins: tuple[str, ...]
    review_timeout_hours: int = 72


def parse_origins(raw: str) -> tuple[str, ...]:
    """Split a comma-separated origin list, dropping blanks, duplicates, and ``*``.

    A wildcard is never a valid configured origin. Silently dropping it keeps a
    misconfiguration from widening CORS to every site.
    """

    seen: set[str] = set()
    origins: list[str] = []
    for candidate in raw.split(","):
        origin = candidate.strip().rstrip("/")
        if not origin or origin == "*" or origin in seen:
            continue
        seen.add(origin)
        origins.append(origin)
    return tuple(origins)


def load_settings() -> Settings:
    """Build settings from the Lambda environment.

    ``allowed_origins`` is empty when no origins are configured. Callers must treat an
    empty tuple as "no browser origin is allowed" rather than falling back to ``*``.
    """

    return Settings(
        stage=os.environ.get("STAGE", "dev"),
        region=os.environ.get("AWS_REGION", "us-east-1"),
        reports_table=os.environ.get("REPORTS_TABLE", "FixFlowReports"),
        events_table=os.environ.get("EVENTS_TABLE", "FixFlowReportEvents"),
        tokens_table=os.environ.get("TOKENS_TABLE", "FixFlowWorkflowTokens"),
        evidence_bucket=os.environ.get("EVIDENCE_BUCKET", ""),
        web_bucket=os.environ.get("WEB_BUCKET", ""),
        allowed_origins=parse_origins(
            os.environ.get("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
        ),
        review_timeout_hours=int(os.environ.get("REVIEW_TIMEOUT_HOURS", "72")),
    )
