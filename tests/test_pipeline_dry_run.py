"""End-to-end dry run against the sample CSV — no WordPress, no real LLM."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from conftest import SAMPLE_CSV

from wfh_pipeline.generation.base import LLMBackend
from wfh_pipeline.generation.generator import ContentGenerator
from wfh_pipeline.pipeline import Pipeline, compute_schedule
from wfh_pipeline.sources.csv_source import CSVLeadSource
from wfh_pipeline.state import PostedStore

FAKE_PAYLOAD = {
    "seo_title": "Remote Jobs Hiring Now: Verified Lead",
    "slug": "remote-jobs-hiring-now-verified-lead",
    "meta_description": "A verified work-from-home job lead with requirements, pay details, and a direct apply link.",
    "focus_keyword": "remote jobs hiring now",
    "body_html": "<p>Opening paragraph about the role.</p>"
    + "<p>Substantive filler paragraph so the body passes QC length checks.</p>" * 30,
    "excerpt": "A verified work-from-home job lead with a direct apply link.",
}


class FakeJSONBackend(LLMBackend):
    """Always returns the same valid JSON payload — no network, no cost."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system: str, prompt: str, max_tokens: int = 8192) -> str:
        self.calls += 1
        return json.dumps(FAKE_PAYLOAD)


def _make_pipeline(tmp_path: Path) -> tuple[Pipeline, PostedStore, FakeJSONBackend]:
    backend = FakeJSONBackend()
    store = PostedStore(tmp_path / "state.db")
    pipeline = Pipeline(
        source=CSVLeadSource(SAMPLE_CSV),
        generator=ContentGenerator(backend),
        store=store,
        wordpress=None,  # dry runs must never need WordPress
        timezone="America/New_York",
        schedule_times=("08:00", "12:00", "16:00"),
    )
    return pipeline, store, backend


def test_dry_run_processes_all_verified_leads_without_wordpress(tmp_path: Path) -> None:
    pipeline, store, backend = _make_pipeline(tmp_path)
    report = pipeline.run(limit=10, status="draft", dry_run=True, lane="all")

    assert report.count("dry_run") == 4  # the unverified row never enters the pipeline
    assert backend.calls == 4
    assert store.count() == 0  # dry runs must not mark leads as posted

    for result in report.results:
        assert result.post is not None
        assert len(result.post.seo_title) <= 60
        assert '<script type="application/ld+json">' in result.content_html
        assert "Affiliate disclosure" in result.content_html
    store.close()


def test_dry_run_skips_already_posted_leads(tmp_path: Path) -> None:
    leads = CSVLeadSource(SAMPLE_CSV).fetch_new_leads()
    pipeline, store, backend = _make_pipeline(tmp_path)
    store.record(leads[0].id, 1, "https://example.com/?p=1")

    report = pipeline.run(limit=10, status="draft", dry_run=True, lane="all")
    assert report.count("skipped_already_posted") == 1
    assert report.count("dry_run") == 3
    assert backend.calls == 3  # no LLM spend on duplicates
    store.close()


def test_limit_caps_processed_leads(tmp_path: Path) -> None:
    pipeline, store, backend = _make_pipeline(tmp_path)
    report = pipeline.run(limit=1, status="draft", dry_run=True, lane="all")
    assert report.count("dry_run") == 1
    assert backend.calls == 1
    store.close()


def test_schedule_assigns_increasing_future_slots(tmp_path: Path) -> None:
    pipeline, store, _ = _make_pipeline(tmp_path)
    report = pipeline.run(limit=3, status="draft", schedule=True, dry_run=True, lane="all")
    scheduled = [r.scheduled_for for r in report.results if r.action == "dry_run"]
    assert len(scheduled) == 3
    assert all(slot is not None and slot.tzinfo is not None for slot in scheduled)
    assert scheduled == sorted(scheduled)
    store.close()


def test_compute_schedule_spreads_across_days() -> None:
    tz = ZoneInfo("America/New_York")
    now = datetime(2026, 6, 9, 10, 0, tzinfo=tz)  # a Tuesday, 10:00 ET
    slots = compute_schedule(
        5, tz_name="America/New_York", times=("08:00", "12:00", "16:00"), now=now
    )
    assert [slot.strftime("%Y-%m-%d %H:%M") for slot in slots] == [
        "2026-06-09 12:00",
        "2026-06-09 16:00",
        "2026-06-10 08:00",
        "2026-06-10 12:00",
        "2026-06-10 16:00",
    ]
    assert all(slot.tzinfo is not None for slot in slots)
