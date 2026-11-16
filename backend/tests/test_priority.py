from __future__ import annotations

import pytest

from shared.priority import calculate_priority, priority_band, priority_score


@pytest.mark.parametrize(
    ("category", "severity", "score", "priority"),
    [
        ("WATER_LEAK", "CRITICAL", 70, "P1_URGENT"),
        ("DRAINAGE", "HIGH", 55, "P2_HIGH"),
        ("POTHOLE", "HIGH", 50, "P2_HIGH"),
        ("POTHOLE", "MEDIUM", 35, "P3_NORMAL"),
        ("WASTE", "HIGH", 40, "P2_HIGH"),
        ("STREETLIGHT", "LOW", 20, "P3_NORMAL"),
        ("OTHER", "LOW", 10, "P4_LOW"),
    ],
)
def test_documented_worked_examples(
    category: str, severity: str, score: int, priority: str
) -> None:
    assert calculate_priority(category, severity) == (score, priority)


@pytest.mark.parametrize(
    ("score", "priority"),
    [
        (0, "P4_LOW"),
        (19, "P4_LOW"),
        (20, "P3_NORMAL"),
        (39, "P3_NORMAL"),
        (40, "P2_HIGH"),
        (59, "P2_HIGH"),
        (60, "P1_URGENT"),
        (100, "P1_URGENT"),
    ],
)
def test_band_boundaries(score: int, priority: str) -> None:
    assert priority_band(score) == priority


def test_unknown_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        priority_score("POTHOLE", "EXTREME")
    with pytest.raises(ValueError):
        priority_score("UNKNOWN", "LOW")
