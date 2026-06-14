"""Command-line entrypoint: ``python -m wfh_pipeline [OPTIONS]``."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer

from .config import ConfigError, Settings
from .generation.base import LLMBackend
from .generation.generator import ContentGenerator
from .logging_setup import setup_logging
from .pipeline import Lane, LeadResult, Pipeline, PipelineReport
from .relevance import RelevanceFilter
from .sources.base import LeadSource
from .sources.csv_source import CSVLeadSource
from .state import PostedStore
from .wordpress import WordPressClient

app = typer.Typer(
    add_completion=False,
    help="Automated WordPress content pipeline for thewfhconnect.com",
)


class SourceKind(str, Enum):
    sheets = "sheets"
    csv = "csv"


class SourceGroup(str, Enum):
    """Which source group to pull from when using multi-source mode.

    * ``direct``      — ATS boards configured in boards.yaml (Greenhouse + Lever)
    * ``aggregator``  — RSS feeds (RemoteOK, WWR, Remotive)
    * ``all``         — every enabled source in boards.yaml + .env (default)
    """
    direct = "direct"
    aggregator = "aggregator"
    all = "all"


class PostStatus(str, Enum):
    draft = "draft"
    publish = "publish"
    future = "future"


class LaneOption(str, Enum):
    direct = "direct"
    aggregator = "aggregator"
    all = "all"


def _build_legacy_source(kind: SourceKind, csv_path: Path, settings: Settings) -> LeadSource:
    """Build a single-file source (csv or sheets) for backward compatibility."""
    if kind is SourceKind.csv:
        return CSVLeadSource(csv_path)
    if not hasattr(settings, "require_sheets"):
        raise ConfigError("Google Sheets source requires GOOGLE_SHEETS_ID and service account.")
    settings.require_sheets()  # type: ignore[attr-defined]
    from .sources.sheets import GoogleSheetsLeadSource  # lazy: gspread only needed here

    return GoogleSheetsLeadSource(
        settings.google_sheets_id,
        settings.google_sheets_worksheet,
        settings.google_service_account_json,
    )


def _build_multi_source(group: SourceGroup, settings: Settings) -> LeadSource:
    """Build a MultiLeadSource from boards.yaml + .env config."""
    from .sources.multi import build_sources

    return build_sources(settings, source_group=group.value)


def _build_backend(settings: Settings) -> LLMBackend:
    settings.require_llm()
    if settings.llm_backend == "ollama":
        from .generation.ollama_backend import OllamaBackend

        return OllamaBackend(settings.ollama_base_url, settings.ollama_model)
    from .generation.anthropic_backend import AnthropicBackend

    return AnthropicBackend(settings.anthropic_api_key, settings.anthropic_model)


@app.command()
def run(
    # ── Source selection ──────────────────────────────────────────────────────
    source: Optional[SourceKind] = typer.Option(
        None,
        "--source",
        help=(
            "Single-file source mode: 'csv' or 'sheets'. "
            "Mutually exclusive with --source-group."
        ),
    ),
    source_group: SourceGroup = typer.Option(
        SourceGroup.all,
        "--source-group",
        help=(
            "Multi-source mode: 'direct' (ATS boards only), "
            "'aggregator' (RSS feeds only), or 'all' (default: everything in boards.yaml)."
        ),
    ),
    csv_path: Path = typer.Option(
        Path("sample_leads.csv"), "--csv-path", help="CSV file when --source csv."
    ),
    # ── Run behaviour ─────────────────────────────────────────────────────────
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, help="Max posts this run (default: POSTS_PER_RUN)."
    ),
    status: Optional[PostStatus] = typer.Option(
        None,
        "--status",
        help="WordPress post status (default: DEFAULT_POST_STATUS, normally draft).",
    ),
    schedule: bool = typer.Option(
        False,
        "--schedule",
        help="Spread posts across SCHEDULE_TIMES as status=future.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run everything (including the LLM) but do not touch WordPress.",
    ),
    lane: LaneOption = typer.Option(
        LaneOption.direct,
        "--lane",
        help=(
            "Which lead lane to process. "
            "'direct' (default) = ATS links only; "
            "'aggregator' = job-board links only; "
            "'all' = process everything."
        ),
    ),
    resolve_source_links: bool = typer.Option(
        False,
        "--resolve-source-links",
        help=(
            "Attempt to extract the employer's real apply URL from aggregator listings. "
            "Only upgrades the URL when it resolves to a known-direct ATS domain."
        ),
    ),
    no_relevance_filter: bool = typer.Option(
        False,
        "--no-relevance-filter",
        help=(
            "Disable the title/description relevance filter. "
            "By default the filter skips senior/engineering roles and requires "
            "at least one entry-level / CS / data-entry keyword. "
            "Set to pass all leads through regardless of title."
        ),
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Fetch verified leads, generate SEO posts, and publish them to WordPress.

    Default (recommended daily run)::

        python -m wfh_pipeline run --dry-run

    This pulls from all ATS boards in boards.yaml and RSS feeds, applies the
    direct lane filter (ATS leads only by default), generates copy, and shows
    a dry-run preview without touching WordPress.

    Typical automated run (direct lane, real publish as draft)::

        python -m wfh_pipeline run --lane direct --source-group direct --status draft

    Aggregator review session (inspect RSS leads, post as draft for manual review)::

        python -m wfh_pipeline run --lane aggregator --source-group aggregator --status draft
    """
    try:
        settings = Settings.load()
    except ConfigError as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    setup_logging(verbose=verbose, log_file=settings.log_file)

    effective_status = status.value if status else settings.default_post_status
    effective_limit = limit if limit is not None else settings.posts_per_run

    wordpress: WordPressClient | None = None
    try:
        # Source: explicit --source flag takes precedence (backward compat)
        if source is not None:
            lead_source = _build_legacy_source(source, csv_path, settings)
        else:
            lead_source = _build_multi_source(source_group, settings)

        generator = ContentGenerator(_build_backend(settings), settings.affiliate_links)
        if not dry_run:
            settings.require_wordpress()
            wordpress = WordPressClient(
                settings.wp_url, settings.wp_username, settings.wp_app_password
            )
        store = PostedStore(settings.db_path)
    except ConfigError as exc:
        typer.secho(f"Config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    # Relevance filter: on by default; disable with --no-relevance-filter.
    relevance_filter: RelevanceFilter | None = None
    if not no_relevance_filter:
        relevance_filter = RelevanceFilter.from_env()

    pipeline = Pipeline(
        source=lead_source,
        generator=generator,
        store=store,
        wordpress=wordpress,
        timezone=settings.timezone,
        schedule_times=settings.schedule_times,
        allow_aggregator_autopublish=settings.allow_aggregator_autopublish,
        relevance_filter=relevance_filter,
    )
    try:
        report = pipeline.run(
            limit=effective_limit,
            status=effective_status,
            schedule=schedule,
            dry_run=dry_run,
            lane=lane.value,  # type: ignore[arg-type]
            resolve_source_links=resolve_source_links,
        )
    finally:
        store.close()
        if wordpress is not None:
            wordpress.close()

    _print_report(report)
    if report.count("error") and not (report.count("posted") or report.count("dry_run")):
        raise typer.Exit(code=1)


def _print_report(report: PipelineReport) -> None:
    for result in report.results:
        if result.action == "dry_run" and result.post is not None:
            _print_preview(result)
    typer.echo()
    typer.secho(f"Done: {report.summary()}", bold=True)
    for result in report.results:
        line = f"  [{result.action}] {result.label}"
        if result.wp_url:
            line += f" -> {result.wp_url}"
        if result.scheduled_for:
            line += f" @ {result.scheduled_for:%Y-%m-%d %H:%M %Z}"
        if result.detail:
            line += f" ({result.detail})"
        typer.echo(line)


def _print_preview(result: LeadResult) -> None:
    post = result.post
    assert post is not None
    sep = "-" * 60
    typer.secho(sep, dim=True)
    typer.secho(f"DRAFT PREVIEW: {result.label}", bold=True)
    typer.echo(f"  Title:    {post.seo_title}")
    typer.echo(f"  Slug:     {post.slug}")
    typer.echo(f"  Keyword:  {post.focus_keyword}")
    typer.echo(f"  Excerpt:  {post.excerpt}")
    if result.scheduled_for:
        typer.echo(f"  Schedule: {result.scheduled_for:%Y-%m-%d %H:%M %Z}")
    typer.secho(sep, dim=True)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
