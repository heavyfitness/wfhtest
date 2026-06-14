"""Orchestrator: fetch leads -> relevance filter -> generate -> QC -> schema -> publish -> record."""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from .generation.generator import ContentGenerator
from .models import GeneratedPost, Lead
from .qc import validate_post
from .relevance import RelevanceFilter
from .schema import append_jobposting_schema
from .sources.base import LeadSource
from .state import PostedStore
from .utils import humanize_category
from .wordpress import WordPressClient, WordPressError

logger = logging.getLogger(__name__)

Action = Literal["posted", "dry_run", "skipped_already_posted", "skipped_qc", "error"]
Lane = Literal["direct", "aggregator", "all"]


@dataclass
class LeadResult:
    lead_id: str
    label: str  # "Company -- Title"
    action: Action
    detail: str = ""
    wp_post_id: int | None = None
    wp_url: str = ""
    scheduled_for: datetime | None = None
    post: GeneratedPost | None = None
    content_html: str = ""  # final body incl. JSON-LD -- exactly what goes to WP


@dataclass
class PipelineReport:
    results: list[LeadResult] = field(default_factory=list)

    def count(self, action: Action) -> int:
        return sum(1 for result in self.results if result.action == action)

    def summary(self) -> str:
        counts = Counter(result.action for result in self.results)
        if not counts:
            return "nothing to do (no new verified leads)"
        return ", ".join(f"{count} {action}" for action, count in sorted(counts.items()))


def compute_schedule(
    count: int,
    *,
    tz_name: str,
    times: Sequence[str],
    now: datetime | None = None,
) -> list[datetime]:
    """Next ``count`` publish slots, spreading posts across ``times`` each day.

    Slots earlier than now+5min are skipped, so a 9am run with times
    08:00/12:00/16:00 schedules today 12:00, today 16:00, tomorrow 08:00, ...
    ``now``, when given, must be timezone-aware.
    """
    tz = ZoneInfo(tz_name)
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    slot_times = sorted(dt_time(int(entry[:2]), int(entry[3:5])) for entry in times)
    slots: list[datetime] = []
    day = current.date()
    while len(slots) < count:
        for slot_time in slot_times:
            candidate = datetime.combine(day, slot_time, tzinfo=tz)
            if candidate > current + timedelta(minutes=5):
                slots.append(candidate)
                if len(slots) == count:
                    break
        day += timedelta(days=1)
    return slots


class Pipeline:
    """Wires a lead source, generator, dedup store, and WP client together.

    ``wordpress`` may be None for dry runs -- the pipeline refuses to do a real
    run without it.

    ``relevance_filter`` is an optional :class:`~wfh_pipeline.relevance.RelevanceFilter`
    applied after lane filtering to skip roles outside the target audience (e.g.
    senior engineering titles when targeting entry-level CS/data-entry candidates).
    Pass ``RelevanceFilter.from_env()`` to activate it from environment variables.
    """

    def __init__(
        self,
        *,
        source: LeadSource,
        generator: ContentGenerator,
        store: PostedStore,
        wordpress: WordPressClient | None = None,
        timezone: str = "America/New_York",
        schedule_times: Sequence[str] = ("08:00", "12:00", "16:00"),
        allow_aggregator_autopublish: bool = False,
        relevance_filter: RelevanceFilter | None = None,
    ) -> None:
        self._source = source
        self._generator = generator
        self._store = store
        self._wordpress = wordpress
        self._timezone = timezone
        self._schedule_times = tuple(schedule_times)
        self._allow_aggregator_autopublish = allow_aggregator_autopublish
        self._relevance_filter = relevance_filter

    def run(
        self,
        *,
        limit: int | None = None,
        status: str = "draft",
        schedule: bool = False,
        dry_run: bool = False,
        lane: Lane = "direct",
        resolve_source_links: bool = False,
    ) -> PipelineReport:
        report = PipelineReport()
        leads = self._source.fetch_new_leads()
        eligible = [lead for lead in leads if lead.verified]  # belt and braces

        fresh: list[Lead] = []
        for lead in eligible:
            if self._store.is_posted(lead.id):
                logger.info(
                    "Skipping already-posted lead %s (%s -- %s)",
                    lead.id, lead.company, lead.title,
                )
                report.results.append(
                    LeadResult(lead.id, f"{lead.company} — {lead.title}",
                               "skipped_already_posted")
                )
            else:
                fresh.append(lead)

        # Optional: try to extract real employer apply URLs from aggregator pages.
        if resolve_source_links:
            from .link_resolver import try_resolve_lead
            resolved: list[Lead] = []
            for lead in fresh:
                resolved.append(try_resolve_lead(lead))
            fresh = resolved

        # Lane filter: route leads to the correct publishing lane based on
        # whether the apply URL resolves to a direct ATS domain.
        if lane != "all":
            before = len(fresh)
            if lane == "direct":
                fresh = [lead for lead in fresh if lead.is_direct]
            else:  # lane == "aggregator"
                fresh = [lead for lead in fresh if not lead.is_direct]
            skipped = before - len(fresh)
            if skipped:
                logger.info(
                    "Lane filter %r: skipped %d lead(s) (wrong lane)", lane, skipped
                )

        # Relevance filter: skip roles outside the target audience.
        # Applied after lane filter so we log accurately (lane rejects aren't counted).
        if self._relevance_filter is not None and fresh:
            before = len(fresh)
            fresh = self._relevance_filter.filter_leads(fresh)
            skipped = before - len(fresh)
            if skipped:
                logger.info(
                    "Relevance filter: skipped %d lead(s) (off-target title/description)",
                    skipped,
                )

        if limit is not None:
            fresh = fresh[:limit]

        if schedule and status != "future":
            logger.info("--schedule given; forcing status=future")
        use_future = schedule or status == "future"
        effective_status = "future" if use_future else status
        slots = (
            iter(compute_schedule(len(fresh), tz_name=self._timezone,
                                  times=self._schedule_times))
            if use_future
            else None
        )

        if not dry_run and fresh and self._wordpress is None:
            raise RuntimeError("A WordPress client is required unless dry_run=True")

        for lead in fresh:
            label = f"{lead.company} — {lead.title}"

            # Aggregator autopublish guard -- downgrade to draft when the lead's
            # apply URL is not direct and ALLOW_AGGREGATOR_AUTOPUBLISH is off.
            lead_status = effective_status
            if not lead.is_direct and not self._allow_aggregator_autopublish:
                if lead_status != "draft":
                    logger.info(
                        "AGGREGATOR -- needs manual direct-link review before publishing"
                        " (%s); forcing status=draft", label,
                    )
                    lead_status = "draft"
                else:
                    logger.info(
                        "AGGREGATOR -- needs manual direct-link review before publishing"
                        " (%s)", label,
                    )

            try:
                post = self._generator.generate(lead)
            except Exception as exc:  # any backend/parse failure: skip, don't crash the run
                logger.exception("Content generation failed for %s", label)
                report.results.append(LeadResult(lead.id, label, "error", detail=str(exc)))
                continue

            problems = validate_post(lead, post)
            if problems:
                logger.warning("QC rejected %s: %s", label, "; ".join(problems))
                report.results.append(
                    LeadResult(lead.id, label, "skipped_qc",
                               detail="; ".join(problems), post=post)
                )
                continue

            content_html = append_jobposting_schema(post.body_html, lead)
            scheduled_for = next(slots) if slots is not None else None

            if dry_run:
                logger.info(
                    "[dry-run] Would publish %r as %s%s",
                    post.seo_title,
                    lead_status,
                    f" at {scheduled_for:%Y-%m-%d %H:%M %Z}" if scheduled_for else "",
                )
                report.results.append(
                    LeadResult(lead.id, label, "dry_run", post=post,
                               content_html=content_html, scheduled_for=scheduled_for)
                )
                continue

            try:
                report.results.append(
                    self._publish(lead, post, content_html,
                                  status=lead_status, scheduled_for=scheduled_for)
                )
            except WordPressError as exc:
                logger.error("WordPress publish failed for %s: %s", label, exc)
                report.results.append(
                    LeadResult(lead.id, label, "error", detail=str(exc), post=post)
                )

        logger.info("Pipeline finished: %s", report.summary())
        return report

    def _publish(
        self,
        lead: Lead,
        post: GeneratedPost,
        content_html: str,
        *,
        status: str,
        scheduled_for: datetime | None,
    ) -> LeadResult:
        assert self._wordpress is not None

        categories: list[int] = []
        tags: list[int] = []
        try:
            categories = [self._wordpress.get_or_create_category(humanize_category(lead.category))]
            tags = [
                self._wordpress.get_or_create_tag(lead.company),
                self._wordpress.get_or_create_tag(humanize_category(lead.category)),
            ]
        except WordPressError as exc:
            # Terms are nice-to-have; never block a post on taxonomy trouble.
            logger.warning("Could not resolve terms for %s (publishing without): %s",
                           lead.id, exc)

        meta = {
            "rank_math_title": post.seo_title,
            "rank_math_description": post.meta_description,
            "rank_math_focus_keyword": post.focus_keyword,
        }
        data = self._wordpress.create_post(
            title=post.seo_title,
            content=content_html,
            excerpt=post.excerpt,
            status=status,
            slug=post.slug,
            categories=categories,
            tags=tags,
            meta=meta,
            scheduled_for=scheduled_for,
        )
        wp_post_id = int(data["id"]) if data.get("id") else None
        wp_url = str(data.get("link", ""))
        self._store.record(lead.id, wp_post_id, wp_url)
        logger.info("Published %s -> %s (%s)", lead.id, wp_url or wp_post_id, status)
        return LeadResult(
            lead.id,
            f"{lead.company} — {lead.title}",
            "posted",
            wp_post_id=wp_post_id,
            wp_url=wp_url,
            scheduled_for=scheduled_for,
            post=post,
        )
