"""Extract scheduled services from the authenticated PetCheck dashboard."""

from __future__ import annotations

import re
import time as system_time
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

from petcheck_calendar.calendar import Walk
from petcheck_calendar.settings import Settings

TIME_PATTERN = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.IGNORECASE)
TIME_PREFIX_PATTERN = re.compile(
    r"^\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r"(?:\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))?\s*",
    re.IGNORECASE,
)
DETAIL_URL_TEMPLATE = (
    "https://dashboard.petchecktechnology.com/scheduler/detail/{source_id}"
)
DATE_PATTERN = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|"
    r"July?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2}(?:,\s*\d{4})?\b",
    re.IGNORECASE,
)


class ScrapeError(RuntimeError):
    """Raised when PetCheck cannot provide usable appointments."""


@dataclass(frozen=True, slots=True)
class RawAppointment:
    """Browser-extracted appointment fields."""

    text: str
    date_hint: str = ""
    starts_at: str = ""
    ends_at: str = ""
    source_id: str = ""
    url: str = ""


def parse_appointment(
    raw: RawAppointment,
    *,
    timezone: ZoneInfo,
    default_duration: timedelta,
    default_summary: str,
    reference: datetime,
) -> Walk | None:
    """Convert browser text and data attributes into a walk."""
    if _is_cancelled(raw):
        return None

    starts_at = _parse_datetime(raw.starts_at, timezone, reference)
    ends_at = _parse_datetime(raw.ends_at, timezone, reference)
    times = TIME_PATTERN.findall(raw.text)

    if starts_at is None:
        date_text = raw.date_hint
        if not date_text:
            match = DATE_PATTERN.search(raw.text)
            date_text = match.group(0) if match else ""
        if not date_text or not times:
            return None
        starts_at = _parse_datetime(f"{date_text} {times[0]}", timezone, reference)
    if starts_at is None:
        return None

    if ends_at is None and len(times) > 1:
        ends_at = _parse_datetime(
            f"{starts_at.date().isoformat()} {times[1]}", timezone, reference
        )
        if ends_at is not None and ends_at <= starts_at:
            ends_at += timedelta(days=1)
    if ends_at is None:
        ends_at = starts_at + default_duration

    source_id = (
        raw.source_id
        or sha256(f"{starts_at.isoformat()}|{raw.text}".encode()).hexdigest()[:24]
    )
    description_lines = []
    for line in raw.text.splitlines():
        description_line = TIME_PREFIX_PATTERN.sub("", line).strip()
        if description_line:
            description_lines.append(description_line)
    summary = _walk_summary(description_lines, default_summary)
    if raw.url:
        description_lines.append(raw.url)
    return Walk(
        starts_at=starts_at,
        ends_at=ends_at,
        summary=summary,
        description="\n".join(description_lines),
        source_id=source_id,
        url=raw.url,
    )


def _walk_summary(description_lines: list[str], default_summary: str) -> str:
    if len(description_lines) < 2:
        return default_summary
    walker_first_name = description_lines[0].split(maxsplit=1)[0]
    pet_first_name = description_lines[1].split(maxsplit=1)[0]
    return f"Dog Walk - {pet_first_name} [{walker_first_name}]"


def _is_cancelled(raw: RawAppointment) -> bool:
    lowered = raw.text.casefold()
    return any(status in lowered for status in ("cancelled", "canceled", "declined"))


def _parse_datetime(
    value: str, timezone: ZoneInfo, reference: datetime
) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(
            value,
            default=reference.replace(tzinfo=None, minute=0, second=0, microsecond=0),
        )
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone)
    return parsed.astimezone(timezone)


def save_login(settings: Settings) -> None:
    """Open PetCheck for interactive login and save the resulting browser session."""
    state_path = settings.browser_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(settings.dashboard_url, wait_until="domcontentloaded")
        print("Log in to PetCheck in the browser window, then return here.")
        input("Press Enter after the dashboard calendar is visible: ")
        if _is_login_page(page):
            browser.close()
            raise ScrapeError("PetCheck still shows the login page")
        _save_storage_state(context, state_path)
        browser.close()


def scrape_walks(
    settings: Settings, *, headless: bool = True, diagnostics: bool = False
) -> list[Walk]:
    """Scrape appointments in the configured rolling date window."""
    state_path = settings.browser_state_path
    has_credentials = bool(
        settings.petcheck_username and settings.petcheck_password.get_secret_value()
    )
    if not state_path.exists() and not has_credentials:
        raise ScrapeError(
            "no saved login or PetCheck credentials; run 'petcheck-calendar login' "
            "or configure PETCHECK_CALENDAR_USERNAME and "
            "PETCHECK_CALENDAR_PASSWORD"
        )

    timezone = ZoneInfo(settings.timezone)
    now = datetime.now(timezone)
    range_start = datetime.combine(now.date(), time.min, tzinfo=timezone)
    range_end = range_start + timedelta(days=settings.days_ahead)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context_options: dict[str, object] = {"timezone_id": settings.timezone}
        if state_path.exists():
            context_options["storage_state"] = state_path
        context = browser.new_context(**context_options)
        page = context.new_page()
        target_url = settings.calendar_url or settings.dashboard_url
        page.goto(target_url, wait_until="networkidle")
        if _is_login_page(page):
            _authenticate(page, context, settings)
        raw_items = _extract_months(
            page, range_start.date(), range_end.date(), settings
        )
        if not raw_items and settings.calendar_url:
            page.goto(target_url, wait_until="networkidle")
            raw_items = _extract_items(page, settings.event_selector)
        if diagnostics:
            _save_diagnostics(page, settings.diagnostic_path)
        browser.close()

    parsed_walks: list[Walk] = []
    for raw in raw_items:
        walk = parse_appointment(
            raw,
            timezone=timezone,
            default_duration=timedelta(minutes=settings.default_duration_minutes),
            default_summary=settings.event_summary,
            reference=now,
        )
        if walk is not None:
            parsed_walks.append(walk)
    if (
        raw_items
        and not parsed_walks
        and any(not _is_cancelled(raw) for raw in raw_items)
    ):
        raise ScrapeError(
            "PetCheck returned appointments but none could be parsed; rerun refresh "
            "with --diagnostics and configure PETCHECK_CALENDAR_CALENDAR_URL or "
            "PETCHECK_CALENDAR_EVENT_SELECTOR"
        )
    walks = {
        walk.uid: walk
        for walk in parsed_walks
        if range_start <= walk.starts_at < range_end
    }
    return sorted(walks.values(), key=lambda walk: walk.starts_at)


def _is_login_page(page: Page) -> bool:
    heading = page.get_by_role("heading", name=re.compile("login to petcheck", re.I))
    return heading.count() > 0


def _authenticate(page: Page, context: BrowserContext, settings: Settings) -> None:
    username = settings.petcheck_username
    password = settings.petcheck_password.get_secret_value()
    if not username or not password:
        raise ScrapeError(
            "saved PetCheck login expired and automatic credentials are not configured"
        )

    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.locator('input[type="submit"][name="submit"]').click()
    page.wait_for_load_state("networkidle")
    if _is_login_page(page):
        raise ScrapeError("PetCheck rejected the configured credentials")

    state_path = settings.browser_state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _save_storage_state(context, state_path)


def _save_storage_state(context: BrowserContext, path: Path) -> None:
    context.storage_state(path=path)
    path.chmod(0o600)


def _extract_months(
    page: Page, range_start: date, range_end: date, settings: Settings
) -> list[RawAppointment]:
    appointments: list[RawAppointment] = []
    month = range_start.replace(day=1)
    last_month = range_end.replace(day=1)
    while month <= last_month:
        endpoint = (
            "https://dashboard.petchecktechnology.com/scheduler/schedule/month/"
            f"{month:%m/%Y/%d}"
        )
        response = page.request.post(endpoint)
        if not response.ok:
            raise ScrapeError(
                f"PetCheck month request for {month:%Y-%m} failed with HTTP "
                f"{response.status}"
            )
        payload = response.json()
        if (
            not isinstance(payload, dict)
            or payload.get("result") != "success"
            or not isinstance(payload.get("data"), str)
        ):
            raise ScrapeError(
                f"PetCheck returned an invalid response for {month:%Y-%m}"
            )

        month_page = page.context.new_page()
        month_page.set_content(payload["data"])
        appointments.extend(_extract_items(month_page, settings.event_selector))
        month_page.close()
        month = _next_month(month)
    in_range = [
        item
        for item in appointments
        if _appointment_in_range(item, range_start, range_end)
    ]
    return [_with_service_detail(page, item, settings) for item in in_range]


def _next_month(value: date) -> date:
    if value.month == 12:
        return value.replace(year=value.year + 1, month=1)
    return value.replace(month=value.month + 1)


def _appointment_in_range(
    appointment: RawAppointment, range_start: date, range_end: date
) -> bool:
    try:
        appointment_date = date.fromisoformat(appointment.date_hint)
    except ValueError:
        return False
    return range_start <= appointment_date < range_end


def _extract_items(page: Page, selector: str) -> list[RawAppointment]:
    items: list[RawAppointment] = []
    for element in page.locator(selector).all():
        text_content = element.inner_text().strip()
        if not text_content:
            continue
        source_id = _first_attribute(
            element,
            "walk_id",
            "data-event-id",
            "data-id",
            "id",
            "href",
        )
        is_petcheck_card = "calendar_item" in (element.get_attribute("class") or "")
        detail_url = (
            DETAIL_URL_TEMPLATE.format(source_id=source_id)
            if is_petcheck_card and source_id
            else ""
        )
        items.append(
            RawAppointment(
                text=text_content,
                date_hint=_date_hint(element),
                starts_at=_first_attribute(element, "data-start", "datetime"),
                ends_at=_first_attribute(element, "data-end"),
                source_id=source_id,
                url=detail_url,
            )
        )
    return items


def _with_service_detail(
    page: Page, appointment: RawAppointment, settings: Settings
) -> RawAppointment:
    if not appointment.source_id.isdigit() or not appointment.url:
        return appointment

    cache_path = settings.detail_cache_path / f"{appointment.source_id}.html"
    cached_html = _read_fresh_cache(cache_path, settings.detail_cache_hours)
    if cached_html is None:
        response = page.request.get(appointment.url)
        if response.ok:
            cached_html = response.text()
            enriched_appointment = _apply_detail_html(
                page.context, appointment, cached_html
            )
            _write_cache(cache_path, cached_html)
            return enriched_appointment
        elif cache_path.exists():
            cached_html = cache_path.read_text(encoding="utf-8")
        else:
            raise ScrapeError(
                f"PetCheck detail {appointment.source_id} request failed with HTTP "
                f"{response.status}"
            )
    return _apply_detail_html(page.context, appointment, cached_html)


def _apply_detail_html(
    context: BrowserContext, appointment: RawAppointment, html: str
) -> RawAppointment:
    detail_page = context.new_page()
    detail_page.set_content(html)
    scheduled_field = detail_page.locator('input[name="original_service_date"]')
    duration_label = detail_page.get_by_text("Service Duration:", exact=True).first
    if not scheduled_field.count() or not duration_label.count():
        detail_page.close()
        raise ScrapeError(
            f"PetCheck detail {appointment.source_id} lacks schedule data"
        )

    starts_at = scheduled_field.input_value()
    duration_text = duration_label.evaluate(
        "element => element.nextElementSibling?.innerText.trim() || ''"
    )
    detail_page.close()
    duration = _parse_duration(duration_text)
    try:
        ends_at = date_parser.parse(starts_at) + duration
    except (ValueError, OverflowError) as error:
        raise ScrapeError(
            f"PetCheck detail {appointment.source_id} has invalid start time"
        ) from error
    return replace(
        appointment,
        starts_at=starts_at,
        ends_at=ends_at.isoformat(sep=" "),
    )


def _parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2})", value.strip())
    if not match:
        raise ScrapeError(f"invalid PetCheck service duration: {value!r}")
    hours, minutes, seconds = (int(part) for part in match.groups())
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _read_fresh_cache(path: Path, max_age_hours: int) -> str | None:
    try:
        age_seconds = system_time.time() - path.stat().st_mtime
    except FileNotFoundError:
        return None
    if age_seconds > max_age_hours * 60 * 60:
        return None
    return path.read_text(encoding="utf-8")


def _write_cache(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _first_attribute(element: Locator, *names: str) -> str:
    for name in names:
        value = element.get_attribute(name)
        if value:
            return value
    return ""


def _ancestor_attribute(element: Locator, name: str) -> str:
    ancestor = element.locator(f"xpath=ancestor-or-self::*[@{name}][1]")
    return ancestor.get_attribute(name) or "" if ancestor.count() else ""


def _date_hint(element: Locator) -> str:
    data_date = _ancestor_attribute(element, "data-date")
    if data_date:
        return data_date

    cell = element.locator("xpath=ancestor::td[contains(@class, 'calendar_day')][1]")
    if not cell.count():
        return ""
    date_element = cell.locator(".add_service_item[date]").first
    if not date_element.count():
        return ""
    raw_date = date_element.get_attribute("date") or ""
    try:
        parsed = datetime.strptime("/".join(raw_date.split("/")[:3]), "%m/%d/%y")
        return parsed.date().isoformat()
    except ValueError:
        return ""


def _save_diagnostics(page: Page, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "petcheck-calendar.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=path / "petcheck-calendar.png", full_page=True)
