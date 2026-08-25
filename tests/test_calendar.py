"""Tests for iCalendar generation."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from icalendar import Calendar

from petcheck_calendar.calendar import Walk, render_calendar


def test_render_calendar_preserves_times_and_stable_uid() -> None:
    timezone = ZoneInfo("America/Los_Angeles")
    walk = Walk(
        starts_at=datetime(2026, 9, 3, 11, 30, tzinfo=timezone),
        ends_at=datetime(2026, 9, 3, 12, 0, tzinfo=timezone),
        summary="Dog walk",
        description="PetCheck appointment",
        source_id="appointment-42",
        url="https://dashboard.petchecktechnology.com/scheduler/detail/42",
    )

    payload = render_calendar(
        [walk], generated_at=datetime(2026, 8, 25, 19, 0, tzinfo=UTC)
    )
    parsed = Calendar.from_ical(payload)
    events = [component for component in parsed.walk() if component.name == "VEVENT"]

    assert parsed["X-WR-CALNAME"] == "PetCheck Calendar"
    assert len(events) == 1
    assert events[0]["UID"] == walk.uid
    assert events[0].decoded("DTSTART") == walk.starts_at
    assert events[0].decoded("DTEND") == walk.ends_at
    assert events[0]["URL"] == walk.url
    second_payload = render_calendar(
        [walk], generated_at=datetime(2026, 8, 25, 19, 0, tzinfo=UTC)
    )
    assert second_payload == payload
