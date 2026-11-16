from __future__ import annotations

from shared.config import Settings, load_settings, parse_origins


def test_parse_origins_trims_blanks_and_duplicates() -> None:
    assert parse_origins("http://localhost:5173, https://app.example ,,") == (
        "http://localhost:5173",
        "https://app.example",
    )


def test_parse_origins_rejects_wildcard() -> None:
    # A wildcard is not an origin. Callers must configure exact origins.
    assert parse_origins("*") == ()
    assert parse_origins("http://localhost:5173, *") == ("http://localhost:5173",)
    assert parse_origins("") == ()


def test_load_settings_has_safe_local_defaults(monkeypatch) -> None:
    for name in (
        "STAGE",
        "AWS_REGION",
        "REPORTS_TABLE",
        "EVENTS_TABLE",
        "TOKENS_TABLE",
        "EVIDENCE_BUCKET",
        "WEB_BUCKET",
        "ALLOWED_ORIGINS",
        "WEB_URL",
        "UPLOAD_EXPIRY_SECONDS",
        "REVIEW_TIMEOUT_SECONDS",
        "STATE_MACHINE_ARN",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_settings()

    assert isinstance(settings, Settings)
    assert settings.stage == "dev"
    assert settings.reports_table == "FixFlowReports"
    assert settings.allowed_origins == ("http://localhost:5173",)
    assert settings.web_url == "http://localhost:5173"
    assert settings.review_timeout_seconds == 259200
    assert settings.state_machine_arn == ""


def test_load_settings_reads_template_environment(monkeypatch) -> None:
    monkeypatch.setenv("STAGE", "demo")
    monkeypatch.setenv("REPORTS_TABLE", "Reports")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://demo.example")
    monkeypatch.setenv("WEB_URL", "https://demo.example/")
    monkeypatch.setenv("REVIEW_TIMEOUT_SECONDS", "86400")
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:aws:states:us-east-1:123:stateMachine:fixflow")

    settings = load_settings()

    assert settings.stage == "demo"
    assert settings.reports_table == "Reports"
    assert settings.allowed_origins == ("https://demo.example",)
    assert settings.web_url == "https://demo.example"
    assert settings.review_timeout_seconds == 86400
    assert settings.state_machine_arn.endswith("fixflow")
