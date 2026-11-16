from __future__ import annotations

import json

from shared.logging import REDACTED, log, redact


def test_redact_top_level_and_nested_secrets() -> None:
    value = {
        "reportId": "e3b0c442-98fc-4c14-9afb-f4c8996fb924",
        "taskToken": "opaque",
        "Authorization": "Bearer abc",
        "nested": {"email": "resident@example.test", "status": "REVIEW_PENDING"},
    }

    cleaned = redact(value)

    assert cleaned["reportId"] == value["reportId"]
    assert cleaned["taskToken"] == REDACTED
    assert cleaned["Authorization"] == REDACTED
    assert cleaned["nested"]["email"] == REDACTED
    assert cleaned["nested"]["status"] == "REVIEW_PENDING"


def test_redact_catches_key_fragments_and_camel_case() -> None:
    cleaned = redact(
        {
            "refreshToken": "secret",
            "presignedUrl": "https://example.test/?signature=x",
            "evidenceKey": "evidence/report/photo.jpg",
            "latitude": 1.23,
            "safe": "kept",
        }
    )

    assert cleaned["refreshToken"] == REDACTED
    assert cleaned["presignedUrl"] == REDACTED
    assert cleaned["evidenceKey"] == REDACTED
    assert cleaned["latitude"] == REDACTED
    assert cleaned["safe"] == "kept"


def test_redact_lists_and_does_not_mutate_input() -> None:
    original = {"items": [{"token": "x"}, {"reportId": "r1"}]}
    cleaned = redact(original)

    assert cleaned["items"][0]["token"] == REDACTED
    assert cleaned["items"][1]["reportId"] == "r1"
    assert original["items"][0]["token"] == "x"


def test_log_emits_one_json_line(capsys) -> None:
    log("info", "report submitted", reportId="r1", taskToken="secret")

    line = capsys.readouterr().out.strip()
    payload = json.loads(line)
    assert payload == {
        "level": "INFO",
        "service": "fixflow",
        "message": "report submitted",
        "reportId": "r1",
        "taskToken": REDACTED,
    }
