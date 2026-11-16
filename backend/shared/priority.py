"""Published deterministic priority rules.

Priority is a suggestion, not a safety assessment. Keep the tables in sync with
``docs/data-model.md``; changing them is a product decision.
"""

from __future__ import annotations

SEVERITY_WEIGHTS = {
    "CRITICAL": 40,
    "HIGH": 30,
    "MEDIUM": 15,
    "LOW": 5,
}

CATEGORY_WEIGHTS = {
    "WATER_LEAK": 30,
    "DRAINAGE": 25,
    "POTHOLE": 20,
    "STREETLIGHT": 15,
    "WASTE": 10,
    "OTHER": 5,
}


def priority_score(category: str, severity: str) -> int:
    try:
        return SEVERITY_WEIGHTS[severity] + CATEGORY_WEIGHTS[category]
    except KeyError as exc:
        raise ValueError(f"Unknown category or severity: {exc.args[0]}") from exc


def priority_band(score: int) -> str:
    if score >= 60:
        return "P1_URGENT"
    if score >= 40:
        return "P2_HIGH"
    if score >= 20:
        return "P3_NORMAL"
    return "P4_LOW"


def calculate_priority(category: str, severity: str) -> tuple[int, str]:
    score = priority_score(category, severity)
    return score, priority_band(score)
