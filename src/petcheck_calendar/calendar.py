"""Render PetCheck appointments as an iCalendar feed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

from icalendar import Calendar, Event


@dataclass(frozen=True, slots=True)
class Walk:
    """A scheduled PetCheck service."""

    starts_at: datetime
    ends_at: datetime
    summary: str
    description: str = ""
    source_id: str = ""
    url: str = ""

    def __post_init__(self) -> None:
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("walk times must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("walk end time must be after its start time")

    @property
    def uid(self) -> str:
        """Return a stable identifier so calendar clients update existing events."""
        identity = self.source_id or "|".join(
            (self.starts_at.isoformat(), self.ends_at.isoformat(), self.summary)
        )
        digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
        return f"{digest}@petcheck-calendar.local"


def render_calendar(
    walks: list[Walk],
    *,
    calendar_name: str = "PetCheck Calendar",
    generated_at: datetime | None = None,
) -> bytes:
    """Render walks as an RFC 5545 calendar."""
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")

    calendar = Calendar()
    calendar.add("prodid", "-//petcheck-calendar//PetCheck calendar feed//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("x-wr-calname", calendar_name)
    calendar.add("x-published-ttl", "PT1H")

    for walk in sorted(walks, key=lambda item: item.starts_at):
        event = Event()
        event.add("uid", walk.uid)
        event.add("dtstamp", timestamp.astimezone(UTC))
        event.add("dtstart", walk.starts_at)
        event.add("dtend", walk.ends_at)
        event.add("summary", walk.summary)
        if walk.description:
            event.add("description", walk.description)
        if walk.url:
            event.add("url", walk.url)
        calendar.add_component(event)

    return calendar.to_ical()
