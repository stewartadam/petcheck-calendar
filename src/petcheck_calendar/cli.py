"""Command-line interface for the PetCheck calendar bridge."""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from petcheck_calendar.scraper import ScrapeError, save_login
from petcheck_calendar.service import create_app, ensure_feed_token, refresh_calendar
from petcheck_calendar.settings import Settings

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Publish PetCheck appointments as an ICS feed"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("login", help="save an interactive PetCheck login")
    refresh = subparsers.add_parser("refresh", help="refresh the cached ICS file")
    refresh.add_argument("--diagnostics", action="store_true")
    subparsers.add_parser("serve", help="serve and periodically refresh the feed")
    return parser


def run(args: argparse.Namespace) -> int:
    """Run the requested command."""
    settings = Settings()
    if args.command == "login":
        save_login(settings)
        print(f"Saved PetCheck session to {settings.browser_state_path}")
    elif args.command == "refresh":
        count = refresh_calendar(settings, diagnostics=args.diagnostics)
        print(f"Wrote {count} events to {settings.calendar_path}")
    elif args.command == "serve":
        ensure_feed_token(settings)
        logger.info(
            "calendar feed listening; host=%s port=%d token_path=%s",
            settings.host,
            settings.port,
            settings.feed_token_path,
        )
        uvicorn.run(
            create_app(settings),
            host=settings.host,
            port=settings.port,
            access_log=False,
        )
    return EXIT_SUCCESS


def main() -> int:
    """Run the CLI with top-level error handling."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    try:
        return run(create_parser().parse_args())
    except (ScrapeError, OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
