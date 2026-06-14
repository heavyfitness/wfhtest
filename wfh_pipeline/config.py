"""Environment-driven configuration. Secrets always come from .env / process env."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from .models import AffiliateLink

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-4-6"
VALID_POST_STATUSES = ("draft", "publish", "future")
VALID_LLM_BACKENDS = ("anthropic", "ollama")

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# Default entry-level / CS queries used when JOB_API_QUERIES is not set.
_DEFAULT_JOB_API_QUERIES = (
    "remote customer service no experience",
    "remote data entry no experience",
    "remote chat support entry level",
    "virtual assistant remote",
    "remote customer support associate",
)


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


def _parse_domain_list(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated list of domains from an env var."""
    if not raw:
        return ()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _parse_query_list(raw: str) -> tuple[str, ...]:
    """Parse a comma-separated list of job API search queries from an env var."""
    if not raw.strip():
        return ()
    return tuple(q.strip() for q in raw.split(",") if q.strip())


def load_boards_yaml(boards_path: str | Path | None = None) -> dict[str, Any]:
    """Load boards.yaml from the given path or search common locations.

    Returns an empty dict if the file is not found (all sources disabled).
    """
    candidates: list[Path] = []
    if boards_path:
        candidates.append(Path(boards_path))
    # Auto-discover relative to CWD and this package
    candidates += [
        Path("boards.yaml"),
        Path(__file__).parent.parent / "boards.yaml",
    ]
    for path in candidates:
        if path.is_file():
            try:
                import yaml  # type: ignore[import-untyped]
                with path.open() as fh:
                    return yaml.safe_load(fh) or {}
            except ImportError:
                # PyYAML not installed — fall back to a tiny parser for simple lists
                return _simple_yaml_load(path)
            except Exception:
                return {}
    return {}


def _simple_yaml_load(path: Path) -> dict[str, Any]:
    """Minimal YAML parser for boards.yaml without PyYAML installed.

    Only handles the structure we write: top-level keys with scalar values or
    lists of scalars.  Comments and blank lines are ignored.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    with path.open() as fh:
        for raw_line in fh:
            line = raw_line.split("#")[0].rstrip()  # strip comments
            if not line.strip():
                continue
            if line.startswith("  - ") or line.startswith("- "):
                # List item
                item = line.strip().lstrip("-").strip()
                if current_key is not None:
                    if not isinstance(result.get(current_key), list):
                        result[current_key] = []
                    result[current_key].append(item)
            elif ":" in line and not line.startswith(" "):
                # Top-level key: value
                key, _, val = line.partition(":")
                current_key = key.strip()
                val = val.strip()
                if val in ("true", "false"):
                    result[current_key] = val == "true"
                elif val:
                    result[current_key] = val
                else:
                    # No value yet — will be populated by following items
                    pass
            elif line.startswith("  ") and ":" in line:
                # Nested key under a dict (e.g. rss: or job_api:)
                # Handle simple nested scalar/bool values
                sub_key, _, sub_val = line.strip().partition(":")
                sub_val = sub_val.strip()
                if current_key is not None:
                    if not isinstance(result.get(current_key), dict):
                        result[current_key] = {}
                    if sub_val in ("true", "false"):
                        result[current_key][sub_key.strip()] = sub_val == "true"
                    elif sub_val:
                        result[current_key][sub_key.strip()] = sub_val
    return result


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
    # Link classification — extend the built-in domain lists via .env
    extra_direct_domains: tuple[str, ...]
    extra_aggregator_domains: tuple[str, ...]
    # Lane policy
    allow_aggregator_autopublish: bool
    # Storage & logging
    db_path: Path
    log_file: Path
    # ATS board tokens (from boards.yaml)
    greenhouse_tokens: tuple[str, ...]
    lever_slugs: tuple[str, ...]
    # RSS feed toggles (from boards.yaml)
    rss_remoteok: bool
    rss_weworkremotely: bool
    rss_remotive: bool
    # Optional job API (off by default)
    enable_job_api: bool
    job_api_key: str
    # Entry-level search queries for the job API.
    # Parsed from JOB_API_QUERIES (comma-separated) or falls back to defaults.
    job_api_queries: tuple[str, ...]
    # Path to boards.yaml (auto-discovered when empty)
    boards_yaml_path: str

    @classmethod
    def load(cls, env_file: str | Path | None = None, boards_path: str | Path | None = None) -> "Settings":
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

        # Load boards.yaml for ATS tokens and RSS toggles
        boards = load_boards_yaml(boards_path or _env("BOARDS_YAML_PATH"))
        gh_tokens = tuple(str(t) for t in boards.get("greenhouse", []) if t)
        lever_slugs = tuple(str(s) for s in boards.get("lever", []) if s)
        rss_cfg = boards.get("rss", {}) if isinstance(boards.get("rss"), dict) else {}
        job_api_cfg = boards.get("job_api", {}) if isinstance(boards.get("job_api"), dict) else {}

        # Individual RSS feed toggles — default on if key missing
        rss_remoteok = bool(rss_cfg.get("remoteok", True))
        rss_wwr = bool(rss_cfg.get("weworkremotely", True))
        rss_remotive = bool(rss_cfg.get("remotive", True))

        # Job API — env var takes precedence over boards.yaml
        enable_job_api = _env("ENABLE_JOB_API", "false").lower() in ("1", "true", "yes") or bool(
            job_api_cfg.get("enabled", False)
        )

        # Entry-level search queries — comma-separated list in JOB_API_QUERIES,
        # or use the built-in defaults tuned for WFH Connect's audience.
        raw_queries = _env("JOB_API_QUERIES")
        job_api_queries = _parse_query_list(raw_queries) if raw_queries else _DEFAULT_JOB_API_QUERIES

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
            extra_direct_domains=_parse_domain_list(_env("EXTRA_DIRECT_DOMAINS")),
            extra_aggregator_domains=_parse_domain_list(_env("EXTRA_AGGREGATOR_DOMAINS")),
            allow_aggregator_autopublish=_env("ALLOW_AGGREGATOR_AUTOPUBLISH", "false").lower()
            in ("1", "true", "yes"),
            db_path=Path(_env("DB_PATH", "data/pipeline_state.db")),
            log_file=Path(_env("LOG_FILE", "logs/pipeline.log")),
            greenhouse_tokens=gh_tokens,
            lever_slugs=lever_slugs,
            rss_remoteok=rss_remoteok,
            rss_weworkremotely=rss_wwr,
            rss_remotive=rss_remotive,
            enable_job_api=enable_job_api,
            job_api_key=_env("JOB_API_KEY"),
            job_api_queries=job_api_queries,
            boards_yaml_path=str(boards_path or _env("BOARDS_YAML_PATH")),
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

    def _require(self, *pairs: tuple[str, str]) -> None:
        missing = [name for name, value in pairs if not value]
        if missing:
            raise ConfigError(f"Required env vars not set: {', '.join(missing)}")
