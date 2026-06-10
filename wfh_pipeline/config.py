"""Environment-driven configuration. Secrets always come from .env / process env."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from .models import AffiliateLink

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
VALID_POST_STATUSES = ("draft", "publish", "future")
VALID_LLM_BACKENDS = ("anthropic", "ollama")

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or malformed."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _parse_affiliate_links(raw: str) -> tuple[AffiliateLink, ...]:
    if not raw:
        return ()
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("not a JSON array")
        return tuple(AffiliateLink.model_validate(item) for item in data)
    except (ValueError, TypeError) as exc:
        raise ConfigError(
            "AFFILIATE_LINKS must be a JSON array like "
            '[{"name": "...", "url": "https://...", "blurb": "..."}]'
        ) from exc


@dataclass(frozen=True)
class Settings:
    """All runtime configuration, loaded once from the environment."""

    # WordPress
    wp_url: str
    wp_username: str
    wp_app_password: str
    # LLM
    llm_backend: str
    anthropic_api_key: str
    anthropic_model: str
    ollama_base_url: str
    ollama_model: str
    # Google Sheets
    google_sheets_id: str
    google_sheets_worksheet: str
    google_service_account_json: Path
    # Content
    affiliate_links: tuple[AffiliateLink, ...]
    # Pipeline behaviour
    timezone: str
    posts_per_run: int
    default_post_status: str
    schedule_times: tuple[str, ...]
    # Storage & logging
    db_path: Path
    log_file: Path

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        load_dotenv(env_file or ".env", override=False)

        default_post_status = _env("DEFAULT_POST_STATUS", "draft").lower()
        if default_post_status not in VALID_POST_STATUSES:
            raise ConfigError(
                f"DEFAULT_POST_STATUS must be one of {VALID_POST_STATUSES}, "
                f"got {default_post_status!r}"
            )

        llm_backend = _env("LLM_BACKEND", "anthropic").lower()
        if llm_backend not in VALID_LLM_BACKENDS:
            raise ConfigError(
                f"LLM_BACKEND must be one of {VALID_LLM_BACKENDS}, got {llm_backend!r}"
            )

        timezone = _env("TIMEZONE", "America/New_York")
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"TIMEZONE is not a valid IANA zone: {timezone!r}") from exc

        schedule_times = tuple(
            part.strip()
            for part in _env("SCHEDULE_TIMES", "08:00,12:00,16:00").split(",")
            if part.strip()
        )
        for entry in schedule_times:
            if not _TIME_RE.match(entry):
                raise ConfigError(f"SCHEDULE_TIMES entries must look like HH:MM, got {entry!r}")
        if not schedule_times:
            raise ConfigError("SCHEDULE_TIMES must contain at least one HH:MM time")

        return cls(
            wp_url=_env("WP_URL"),
            wp_username=_env("WP_USERNAME"),
            wp_app_password=_env("WP_APP_PASSWORD"),
            llm_backend=llm_backend,
            anthropic_api_key=_env("ANTHROPIC_API_KEY"),
            anthropic_model=_env("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
            ollama_base_url=_env("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=_env("OLLAMA_MODEL", "llama3.1"),
            google_sheets_id=_env("GOOGLE_SHEETS_ID"),
            google_sheets_worksheet=_env("GOOGLE_SHEETS_WORKSHEET", "Leads"),
            google_service_account_json=Path(
                _env("GOOGLE_SERVICE_ACCOUNT_JSON", "service_account.json")
            ),
            affiliate_links=_parse_affiliate_links(_env("AFFILIATE_LINKS")),
            timezone=timezone,
            posts_per_run=_env_int("POSTS_PER_RUN", 3),
            default_post_status=default_post_status,
            schedule_times=schedule_times,
            db_path=Path(_env("DB_PATH", "data/pipeline_state.db")),
            log_file=Path(_env("LOG_FILE", "logs/pipeline.log")),
        )

    # ── per-operation validation (only demand what a given run actually needs) ──

    def require_wordpress(self) -> None:
        self._require(
            ("WP_URL", self.wp_url),
            ("WP_USERNAME", self.wp_username),
            ("WP_APP_PASSWORD", self.wp_app_password),
        )

    def require_llm(self) -> None:
        if self.llm_backend == "anthropic":
            self._require(("ANTHROPIC_API_KEY", self.anthropic_api_key))

    def require_sheets(self) -> None:
        self._require(("GOOGLE_SHEETS_ID", self.google_sheets_id))
        if not self.google_service_account_json.exists():
            raise ConfigError(
                "Google service account file not found: "
                f"{self.google_service_account_json} (set GOOGLE_SERVICE_ACCOUNT_JSON)"
            )

    @staticmethod
    def _require(*pairs: tuple[str, str]) -> None:
        missing = [name for name, value in pairs if not value]
        if missing:
            raise ConfigError(
                "Missing required settings: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in."
            )
