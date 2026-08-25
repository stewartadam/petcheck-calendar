"""Application settings loaded from environment variables."""

from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration for scraping and serving the calendar."""

    model_config = SettingsConfigDict(
        env_prefix="PETCHECK_CALENDAR_", env_file=".env", extra="ignore"
    )

    dashboard_url: str = "https://dashboard.petchecktechnology.com/"
    calendar_url: str = ""
    timezone: str = "America/Los_Angeles"
    calendar_name: str = "PetCheck Calendar"
    event_summary: str = "Pet service"
    default_duration_minutes: int = Field(default=30, gt=0)
    detail_cache_hours: int = Field(default=24, ge=0)
    days_ahead: int = Field(default=31, gt=0)
    refresh_minutes: int = Field(default=60, gt=0)
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    feed_token: str = ""
    feed_token_path: Path = Path("data/feed-token")
    petcheck_username: str = Field(
        default="", validation_alias="PETCHECK_CALENDAR_USERNAME"
    )
    petcheck_password: SecretStr = Field(
        default=SecretStr(""), validation_alias="PETCHECK_CALENDAR_PASSWORD"
    )
    browser_state_path: Path = Path("data/petcheck-session.json")
    calendar_path: Path = Path("data/walks.ics")
    diagnostic_path: Path = Path("diagnostics")
    detail_cache_path: Path = Path("data/details")
    event_selector: str = (
        ".calendar_item, .fc-event, [data-start], [data-event-id], "
        "[class*='appointment'], [class*='schedule-item']"
    )
