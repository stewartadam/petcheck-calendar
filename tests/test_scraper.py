"""Tests for converting PetCheck markup into calendar events."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from playwright.sync_api import sync_playwright

from petcheck_calendar.scraper import (
    RawAppointment,
    ScrapeError,
    _apply_detail_html,
    _appointment_in_range,
    _extract_items,
    _extract_months,
    _save_storage_state,
    _with_service_detail,
    parse_appointment,
    scrape_walks,
)
from petcheck_calendar.settings import Settings


def test_parse_fullcalendar_appointment() -> None:
    timezone = ZoneInfo("America/Los_Angeles")
    walk = parse_appointment(
        RawAppointment(
            text="11:30 AM - 12:00 PM Pet service",
            date_hint="2026-09-03",
            source_id="visit-42",
        ),
        timezone=timezone,
        default_duration=timedelta(minutes=30),
        default_summary="Dog walk",
        reference=datetime(2026, 8, 25, 10, 0, 42, 123456, tzinfo=timezone),
    )

    assert walk is not None
    assert walk.starts_at == datetime(2026, 9, 3, 11, 30, tzinfo=timezone)
    assert walk.ends_at == datetime(2026, 9, 3, 12, 0, tzinfo=timezone)
    assert walk.summary == "Dog walk"
    assert walk.description == "Pet service"


def test_parse_skips_cancelled_appointment() -> None:
    timezone = ZoneInfo("America/Los_Angeles")
    walk = parse_appointment(
        RawAppointment(text="Sep 3, 2026 11:30 AM Cancelled"),
        timezone=timezone,
        default_duration=timedelta(minutes=30),
        default_summary="Dog walk",
        reference=datetime(2026, 8, 25, tzinfo=timezone),
    )

    assert walk is None


def test_cached_petcheck_detail_sets_exact_start_duration_and_link() -> None:
    month_fixture = Path("tests/fixtures/petcheck-month.html").read_text(
        encoding="utf-8"
    )
    detail_fixture = Path("tests/fixtures/petcheck-detail.html").read_text(
        encoding="utf-8"
    )
    timezone = ZoneInfo("America/Los_Angeles")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()
        page.set_content(month_fixture)
        raw = _extract_items(page, ".calendar_item")[0]
        raw = _apply_detail_html(context, raw, detail_fixture)
        browser.close()

    walk = parse_appointment(
        raw,
        timezone=timezone,
        default_duration=timedelta(minutes=45),
        default_summary="Dog walk",
        reference=datetime(2026, 8, 25, tzinfo=timezone),
    )

    assert walk is not None
    assert walk.starts_at == datetime(2026, 8, 25, 13, 10, tzinfo=timezone)
    assert walk.ends_at == datetime(2026, 8, 25, 13, 55, tzinfo=timezone)
    assert walk.description == (
        "Walker Name\nAtlas\n"
        "https://dashboard.petchecktechnology.com/scheduler/detail/118054756"
    )
    assert walk.url.endswith("/scheduler/detail/118054756")


def test_bare_hour_defaults_to_zero_minutes_without_normalizing() -> None:
    timezone = ZoneInfo("America/Los_Angeles")
    walk = parse_appointment(
        RawAppointment(text="2pm\nWalker Name", date_hint="2026-08-27"),
        timezone=timezone,
        default_duration=timedelta(minutes=45),
        default_summary="Dog walk",
        reference=datetime(2026, 8, 25, 9, 6, tzinfo=timezone),
    )

    assert walk is not None
    assert walk.starts_at == datetime(2026, 8, 27, 14, 0, tzinfo=timezone)


def test_explicit_minutes_are_preserved() -> None:
    timezone = ZoneInfo("America/Los_Angeles")
    walk = parse_appointment(
        RawAppointment(text="1:10pm\nWalker Name", date_hint="2026-08-25"),
        timezone=timezone,
        default_duration=timedelta(minutes=45),
        default_summary="Dog walk",
        reference=datetime(2026, 8, 25, 9, 6, tzinfo=timezone),
    )

    assert walk is not None
    assert walk.starts_at == datetime(2026, 8, 25, 13, 10, tzinfo=timezone)


def test_detail_lookup_date_filter_uses_rolling_window() -> None:
    range_start = datetime(2026, 8, 25).date()
    range_end = datetime(2026, 9, 25).date()

    assert not _appointment_in_range(
        RawAppointment(text="", date_hint="2026-08-24"), range_start, range_end
    )
    assert _appointment_in_range(
        RawAppointment(text="", date_hint="2026-08-25"), range_start, range_end
    )
    assert _appointment_in_range(
        RawAppointment(text="", date_hint="2026-09-24"), range_start, range_end
    )
    assert not _appointment_in_range(
        RawAppointment(text="", date_hint="2026-09-25"), range_start, range_end
    )


def test_scrape_accepts_empty_calendar_but_rejects_unparseable_items(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "petcheck-session.json"
    state_path.write_text("{}", encoding="utf-8")
    settings = Settings(browser_state_path=state_path)
    playwright_context = MagicMock()

    with (
        patch(
            "petcheck_calendar.scraper.sync_playwright",
            return_value=playwright_context,
        ),
        patch("petcheck_calendar.scraper._is_login_page", return_value=False),
        patch("petcheck_calendar.scraper._extract_months") as extract_months,
    ):
        extract_months.return_value = []
        assert scrape_walks(settings) == []

        extract_months.return_value = [RawAppointment(text="unknown appointment")]
        with pytest.raises(ScrapeError, match="none could be parsed"):
            scrape_walks(settings)


def test_saved_browser_state_is_owner_only(tmp_path: Path) -> None:
    state_path = tmp_path / "petcheck-session.json"
    context = MagicMock()
    context.storage_state.side_effect = lambda *, path: path.write_text(
        "{}", encoding="utf-8"
    )

    _save_storage_state(context, state_path)

    assert state_path.stat().st_mode & 0o777 == 0o600


def test_month_extraction_uses_configured_selector() -> None:
    page = MagicMock()
    response = page.request.post.return_value
    response.ok = True
    response.json.return_value = {"result": "success", "data": ""}
    settings = Settings(event_selector="[data-custom-event]")
    range_start = datetime(2026, 8, 1).date()

    with patch("petcheck_calendar.scraper._extract_items", return_value=[]) as extract:
        assert _extract_months(page, range_start, range_start, settings) == []

    extract.assert_called_once_with(
        page.context.new_page.return_value, "[data-custom-event]"
    )


def test_invalid_fresh_detail_response_is_not_cached(tmp_path: Path) -> None:
    appointment = RawAppointment(
        text="appointment",
        source_id="123",
        url="https://example.test/detail/123",
    )
    settings = Settings(detail_cache_path=tmp_path)
    page = MagicMock()
    page.request.get.return_value.ok = True
    page.request.get.return_value.text.return_value = "login page"

    with (
        patch(
            "petcheck_calendar.scraper._apply_detail_html",
            side_effect=ScrapeError("lacks schedule data"),
        ),
        pytest.raises(ScrapeError, match="lacks schedule data"),
    ):
        _with_service_detail(page, appointment, settings)

    assert not (tmp_path / "123.html").exists()
