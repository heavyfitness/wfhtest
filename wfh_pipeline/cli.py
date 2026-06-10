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
from .pipeline import LeadResult, Pipeline, PipelineReport
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


class PostStatus(str, Enum):
    draft = "draft"
    publish = "publish"
    future = "future"


def _build_source(kind: SourceKind, csv_path: Path, settings: Settings) -> LeadSource:
    if kind is SourceKind.csv:
        return CSVLeadSource(csv_path)
    settings.require_sheets()
    from .sources.sheets import GoogleSheetsLeadSource  # lazy: gspread only needed here

    return GoogleSheetsLeadSource(
        settings.google_sheets_id,
        settings.google_sheets_worksheet,
        settings.google_service_account_json,
    )


def _build_backend(settings: Settings) -> LLMBackend:
    settings.require_llm()
    if settings.llm_backend == "ollama":
        from .generation.ollama_backend import OllamaBackend

        return OllamaBackend(settings.ollama_base_url, settings.ollama_model)
    from .generation.anthropic_backend import AnthropicBackend

    return AnthropicBackend(settings.anthropic_api_key, settings.anthropic_model)


@app.command()
def run(
    source: SourceKind = typer.Option(
        SourceKind.csv, "--source", help="Where to read leads from."
    ),
    csv_path: Path = typer.Option(
        Path("sample_leads.csv"), "--csv-path", help="CSV file when --source csv."
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", min=1, help="Max posts this run (default: POSTS_PER_RUN)."
    ),
    status: Optional[PostStatus] = typer.Option(
        None,
        "--status",
        help="WordPress post status (default: DEFAULT_POST_STATUS, normally draft).",
    ),
    schedule: bool = typer.Option(
        False, "--schedule",
        help="Spread posts across SCHEDULE_TIMES as status=future.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Run everything (including the LLM) but do not touch WordPress.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """Fetch verified leads, generate SEO posts, and publish them to WordPress."""
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
        lead_source = _build_source(source, csv_path, settings)
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

    pipeline = Pipeline(
        source=lead_source,
        generator=generator,
        store=store,
        wordpress=wordpress,
        timezone=settings.timezone,
        schedule_times=settings.schedule_times,
    )
    try:
        report = pipeline.run(
            limit=effective_limit,
            status=effective_status,
            schedule=schedule,
            dry_run=dry_run,
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
            line += f" → {result.wp_url}"
        if result.scheduled_for:
            line += f" @ {result.scheduled_for:%Y-%m-%d %H:%M %Z}"
        if result.detail:
            line += f" ({result.detail})"
        typer.echo(line)


def _print_preview(result: LeadResult) -> None:
    post = result.post
    assert post is not None
    has_schema = '<script type="application/ld+json">' in result.content_html
    typer.echo("\n" + "=" * 78)
    typer.secho(f"DRY RUN — {result.label}", bold=True)
    typer.echo("=" * 78)
    typer.echo(f"SEO title:        {post.seo_title}")
    typer.echo(f"Slug:             {post.slug}")
    typer.echo(f"Meta description: {post.meta_description}")
    typer.echo(f"Focus keyword:    {post.focus_keyword}")
    typer.echo(f"Excerpt:          {post.excerpt}")
    if result.scheduled_for:
        typer.echo(f"Scheduled for:    {result.scheduled_for:%Y-%m-%d %H:%M %Z}")
    typer.echo(f"JSON-LD:          {'included' if has_schema else 'skipped (lead too thin)'}")
    typer.echo("-" * 78)
    typer.echo(result.content_html)


if __name__ == "__main__":
    app()
