"""Tests for environment configuration."""

import pytest
from pydantic import ValidationError

from petcheck_calendar.settings import Settings


def test_legacy_petcheck_credential_names_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("USERNAME", "account@example.com")
    monkeypatch.setenv("PASSWORD", "private-value")
    monkeypatch.setenv("WWT_PETCHECK_USERNAME", "account@example.com")
    monkeypatch.setenv("WWT_PETCHECK_PASSWORD", "private-value")

    settings = Settings(_env_file=None)

    assert settings.petcheck_username == ""
    assert settings.petcheck_password.get_secret_value() == ""


def test_namespaced_petcheck_credential_names(monkeypatch) -> None:
    monkeypatch.setenv("PETCHECK_CALENDAR_USERNAME", "account@example.com")
    monkeypatch.setenv("PETCHECK_CALENDAR_PASSWORD", "private-value")

    settings = Settings(_env_file=None)

    assert settings.petcheck_username == "account@example.com"
    assert settings.petcheck_password.get_secret_value() == "private-value"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("default_duration_minutes", 0),
        ("detail_cache_hours", -1),
        ("days_ahead", 0),
        ("refresh_minutes", 0),
        ("port", 0),
        ("port", 65536),
    ],
)
def test_operational_settings_reject_invalid_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})
