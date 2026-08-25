"""Tests for the calendar HTTP endpoint."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import call, patch

import pytest
from fastapi.testclient import TestClient

from petcheck_calendar.scraper import ScrapeError
from petcheck_calendar.service import _refresh_loop, create_app, ensure_feed_token
from petcheck_calendar.settings import Settings


def test_calendar_endpoint_requires_configured_token(tmp_path: Path) -> None:
    calendar_path = tmp_path / "walks.ics"
    calendar_path.write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    app = create_app(
        Settings(calendar_path=calendar_path, feed_token="secret", refresh_minutes=60),
        start_refresh=False,
    )

    with TestClient(app) as client:
        assert client.get("/calendar.ics").status_code == 404
        response = client.get("/calendar.ics?token=secret")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")


def test_health_reports_stale_calendar(tmp_path: Path) -> None:
    calendar_path = tmp_path / "walks.ics"
    calendar_path.write_bytes(b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n")
    app = create_app(
        Settings(
            calendar_path=calendar_path,
            feed_token_path=tmp_path / "feed-token",
            refresh_minutes=60,
        ),
        start_refresh=False,
    )

    with (
        patch("petcheck_calendar.service.time.time", return_value=20_000),
        patch.object(Path, "stat") as stat,
        TestClient(app) as client,
    ):
        stat.return_value.st_mtime = 1_000
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["reason"] == "stale"


def test_generated_feed_token_is_persistent_and_private(tmp_path: Path) -> None:
    token_path = tmp_path / "feed-token"
    first = ensure_feed_token(Settings(feed_token_path=token_path))
    second = ensure_feed_token(Settings(feed_token_path=token_path))

    assert first == second
    assert len(first) >= 32
    assert token_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_refresh_loop_logs_refresh_outcome(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(refresh_minutes=60)

    with (
        patch("petcheck_calendar.service.refresh_calendar", return_value=3),
        patch(
            "petcheck_calendar.service.asyncio.sleep",
            side_effect=asyncio.CancelledError,
        ),
        caplog.at_level(logging.INFO, logger="petcheck_calendar.service"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _refresh_loop(settings)

    messages = [record.getMessage() for record in caplog.records]
    assert "calendar refresh started" in messages
    assert any(
        message.startswith(
            "calendar refresh completed; event_count=3 duration_seconds="
        )
        for message in messages
    )


@pytest.mark.asyncio
async def test_refresh_loop_backs_off_failures_and_resets_after_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = Settings(refresh_minutes=3)

    with (
        patch(
            "petcheck_calendar.service.refresh_calendar",
            side_effect=[
                ScrapeError("failure 1"),
                ScrapeError("failure 2"),
                ScrapeError("failure 3"),
                ScrapeError("failure 4"),
                2,
                ScrapeError("failure after recovery"),
            ],
        ),
        patch(
            "petcheck_calendar.service.asyncio.sleep",
            side_effect=[None, None, None, None, None, asyncio.CancelledError],
        ) as sleep,
        caplog.at_level(logging.ERROR, logger="petcheck_calendar.service"),
        pytest.raises(asyncio.CancelledError),
    ):
        await _refresh_loop(settings)

    assert sleep.await_args_list == [
        call(60),
        call(120),
        call(180),
        call(180),
        call(180),
        call(60),
    ]
    messages = [record.getMessage() for record in caplog.records]
    assert any("retry_delay_seconds=60" in message for message in messages)
    assert any("retry_delay_seconds=120" in message for message in messages)
    assert any("retry_delay_seconds=180" in message for message in messages)
