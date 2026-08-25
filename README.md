---
title: PetCheck Calendar Feed
description: Publish upcoming PetCheck appointments as a private iCalendar feed
ms.date: 2026-08-25
ms.topic: how-to
---

## Overview

This local service signs in to PetCheck through a saved browser session, scrapes
the next 31 days of scheduled services, and publishes them as an ICS calendar
feed. Stable event identifiers let calendar clients update a walk instead of
creating duplicates.

The service keeps the last successful feed when PetCheck is unavailable or the
saved login expires.

## Set up the service

Requirements:

* macOS or Linux
* Python 3.11 or newer
* [uv](https://docs.astral.sh/uv/)

Install the package and Playwright browser:

```bash
uv sync
uv run playwright install chromium
```

Create `.env` from `.env.example`, add the PetCheck credentials, and adjust the
calendar name, timezone, or event duration if needed:

```dotenv
PETCHECK_CALENDAR_USERNAME=account@example.com
PETCHECK_CALENDAR_PASSWORD=replace-with-password
```

The scraper submits these values only to PetCheck's login form. It never writes
them to logs or the generated ICS feed. Protect `.env` with mode `600`; Git and
the Docker build context exclude it.

Save an authenticated browser session:

```bash
uv run petcheck-calendar login
```

Log in directly in the browser window. Once the dashboard is visible, return to
the terminal and press Enter. The session is stored under `data/`, which Git
ignores. This step is optional when credentials are configured. PetCheck's
browser state is short-lived, so the service automatically signs in again when
needed.

Refresh the feed once to verify calendar extraction:

```bash
uv run petcheck-calendar refresh
```

Start the feed server:

```bash
uv run petcheck-calendar serve
```

The default subscription URL is:

```text
webcal://127.0.0.1:8765/calendar.ics?token=YOUR_TOKEN
```

Read the generated token without printing it in application logs:

```bash
cat data/feed-token
```

The server refreshes PetCheck hourly while it is running.

Each event uses the scheduled start time and service duration from its PetCheck
detail page. Detail HTML is cached under `data/details/` for 24 hours, so hourly
feed refreshes do not repeatedly request every service. Set
`PETCHECK_CALENDAR_DETAIL_CACHE_HOURS` to change that interval. Event
descriptions omit the
time and include a direct link to the PetCheck service.

## Deploy with Docker Compose

Docker Compose runs the feed continuously, restarts it after a failure or host
reboot, and stores the generated ICS file plus short-lived PetCheck session
outside the container. Protect the persistent directories and create the
deployment configuration:

```bash
mkdir -p data diagnostics
sudo chown -R 1001:1001 data diagnostics
chmod 700 data diagnostics
cp .env.example .env
chmod 600 .env
```

UID/GID 1001 is the `pwuser` account that runs the service in the container.
The ownership and mode let it update the feed without making the PetCheck
session readable by other server users.

Start the service:

```bash
docker compose up --detach --build
docker compose ps
docker compose logs --follow petcheck-calendar
```

When `PETCHECK_CALENDAR_FEED_TOKEN` is blank, the service creates a persistent
token in `data/feed-token`. Subscribe using the server's LAN hostname or IP
address:

```text
webcal://HOME_SERVER:8765/calendar.ics?token=YOUR_TOKEN
```

Read the generated token on the server without writing it to Compose logs:

```bash
docker exec petcheck-calendar cat /app/data/feed-token
```

HTTP access logging is disabled because calendar subscription URLs contain the
private token.

The host port can be changed with `PETCHECK_CALENDAR_PORT` in `.env`. The
application always listens on port 8765 inside the container.

The Compose deployment includes:

* An hourly PetCheck refresh by default
* An `unless-stopped` restart policy
* A health check that fails when the ICS feed is missing or older than three
  refresh intervals
* Persistent `data/` and `diagnostics/` bind mounts
* A non-root container process

Rebuild and restart a local source deployment after pulling code changes:

```bash
docker compose up --detach --build petcheck-calendar
docker image prune --force
```

GitHub Actions publishes `linux/amd64` and `linux/arm64` images to Docker Hub on
every push to `main`. Jarvis runs `stewartadam/petcheck-calendar:latest`, so it
does not need a Git checkout or build toolchain. Tagged releases such as
`v1.2.3` also publish `1.2.3` and `1.2` image tags.

Update an image-based deployment with:

```bash
docker compose pull petcheck-calendar
docker compose up --detach petcheck-calendar
```

Back up `data/feed-token` and `data/walks.ics`. The session cache can also be
backed up, but configured credentials automatically replace it when needed.
All three files survive image rebuilds because they are stored on the host.

Check operation at any time:

```bash
docker compose ps
docker compose logs --tail 100 petcheck-calendar
curl --fail http://127.0.0.1:8765/health
```

If the container becomes unhealthy and logs report rejected credentials,
update `PETCHECK_CALENDAR_USERNAME` or `PETCHECK_CALENDAR_PASSWORD` in `.env`
and restart the service. The service continues serving the last successful ICS
feed while refresh attempts fail.

For subscriptions outside the home network, publish the endpoint through an
HTTPS reverse proxy. Do not expose port 8765 directly to the internet. Some
calendar providers fetch subscriptions from their own servers, so a LAN-only
address may not work when calendar synchronization is delegated to a cloud
service.

## Subscribe from Apple Calendar

1. Keep `petcheck-calendar serve` running.
2. In Calendar, choose **File > New Calendar Subscription**.
3. Enter `webcal://127.0.0.1:8765/calendar.ics`.
4. Set the auto-refresh interval to every hour.

A localhost subscription works only while this service runs on the same Mac.
For access from other devices, place the service behind an HTTPS reverse proxy,
set `PETCHECK_CALENDAR_HOST=0.0.0.0`, and configure a long random
`PETCHECK_CALENDAR_FEED_TOKEN`. Treat the feed URL as private because it grants
access to appointment times.

## Adjust PetCheck extraction

PetCheck's calendar is visible only after account login, and its markup may
vary by account. The scraper recognizes FullCalendar events and common
appointment attributes by default. If refresh reports no appointments, run:

```bash
uv run petcheck-calendar refresh --diagnostics
```

This writes an HTML snapshot and screenshot to `diagnostics/`. Set these values
in `.env` when the calendar has a dedicated URL or different event element:

```dotenv
PETCHECK_CALENDAR_CALENDAR_URL=https://dashboard.petchecktechnology.com/path/to/calendar
PETCHECK_CALENDAR_EVENT_SELECTOR=.calendar-appointment
```

The selector should match one element per appointment. The parser supports
`data-start`, `data-end`, `data-date`, and visible date/time text.

Use only an account you are authorized to access, poll at a reasonable
interval, and confirm that automation complies with PetCheck's current terms.

## Commands

```text
petcheck-calendar login                  Save a PetCheck browser session
petcheck-calendar refresh               Rebuild data/walks.ics
petcheck-calendar refresh --diagnostics Save calendar HTML and screenshot
petcheck-calendar serve                  Refresh hourly and serve the ICS feed
```

Run checks with:

```bash
uv run prek install
uv run prek run --all-files
```

The project is available under the [MIT License](LICENSE).
