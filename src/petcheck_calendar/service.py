"""Refresh and serve the cached iCalendar feed."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from petcheck_calendar.calendar import render_calendar
from petcheck_calendar.scraper import scrape_walks
from petcheck_calendar.settings import Settings

logger = logging.getLogger(__name__)
INITIAL_RETRY_DELAY_SECONDS = 60


def ensure_feed_token(settings: Settings) -> str:
    """Return the configured token or create a persistent private token."""
    if settings.feed_token:
        return settings.feed_token

    path = settings.feed_token_path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        token = secrets.token_urlsafe(32)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
            token_file.write(f"{token}\n")
    if not token:
        raise ValueError(f"feed token file is empty: {path}")
    settings.feed_token = token
    return token


def refresh_calendar(settings: Settings, *, diagnostics: bool = False) -> int:
    """Scrape PetCheck and atomically replace the cached feed."""
    walks = scrape_walks(settings, diagnostics=diagnostics)
    payload = render_calendar(walks, calendar_name=settings.calendar_name)
    destination = settings.calendar_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(payload)
    temporary.replace(destination)
    return len(walks)


def create_app(
    settings: Settings | None = None, *, start_refresh: bool = True
) -> FastAPI:
    """Create the calendar feed application."""
    config = settings or Settings()
    ensure_feed_token(config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        logger.info(
            "service started; refresh_interval_minutes=%d calendar_path=%s",
            config.refresh_minutes,
            config.calendar_path,
        )
        task = asyncio.create_task(_refresh_loop(config)) if start_refresh else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            logger.info("service stopped")

    app = FastAPI(title="PetCheck Calendar", lifespan=lifespan)

    @app.get("/calendar.ics")
    async def calendar(request: Request) -> Response:
        if config.feed_token and request.query_params.get("token") != config.feed_token:
            raise HTTPException(status_code=404)
        if not config.calendar_path.exists():
            raise HTTPException(
                status_code=503, detail="Calendar has not refreshed yet"
            )
        return Response(
            config.calendar_path.read_bytes(), media_type="text/calendar; charset=utf-8"
        )

    @app.get("/health")
    async def health() -> JSONResponse:
        if not config.calendar_path.exists():
            return JSONResponse({"ready": False, "reason": "missing"}, status_code=503)

        age_seconds = max(0, time.time() - config.calendar_path.stat().st_mtime)
        max_age_seconds = config.refresh_minutes * 60 * 3
        if age_seconds > max_age_seconds:
            return JSONResponse(
                {
                    "ready": False,
                    "reason": "stale",
                    "age_seconds": round(age_seconds),
                },
                status_code=503,
            )
        return JSONResponse(
            {"ready": True, "age_seconds": round(age_seconds)}, status_code=200
        )

    return app


async def _refresh_loop(settings: Settings) -> None:
    refresh_interval_seconds = settings.refresh_minutes * 60
    retry_delay_seconds = 0
    while True:
        started_at = time.monotonic()
        logger.info("calendar refresh started")
        try:
            count = await asyncio.to_thread(refresh_calendar, settings)
            logger.info(
                "calendar refresh completed; event_count=%d duration_seconds=%.2f",
                count,
                time.monotonic() - started_at,
            )
            retry_delay_seconds = 0
            delay_seconds = refresh_interval_seconds
        except Exception:
            retry_delay_seconds = min(
                refresh_interval_seconds,
                retry_delay_seconds * 2
                if retry_delay_seconds
                else INITIAL_RETRY_DELAY_SECONDS,
            )
            delay_seconds = retry_delay_seconds
            logger.exception(
                "calendar refresh failed; retaining previous feed; "
                "duration_seconds=%.2f retry_delay_seconds=%d",
                time.monotonic() - started_at,
                delay_seconds,
            )
        await asyncio.sleep(delay_seconds)
