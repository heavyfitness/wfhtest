"""Tests for lane-based publishing policy in wfh_pipeline.pipeline."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from wfh_pipeline.models import Lead
from wfh_pipeline.pipeline import Pipeline, PipelineReport
from wfh_pipeline.sources.base import LeadSource
from wfh_pipeline.state import PostedStore


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _StaticLeadSource(LeadSource):
    """A lead source that returns a fixed list of leads."""

    name = "static"
    trust = "aggregator"

    def __init__(self, leads: list[Lead]) -> None:
        self._leads = leads

    def fetch_new_leads(self) -> list[Lead]:
        return self._leads


def _fake_generate(lead: Lead) -> MagicMock:
    """Build a minimal GeneratedPost mock that passes QC for the given lead."""
    return MagicMock(
        seo_title="Remote Job at " + lead.company[:20],
        slug="remote-job-" + lead.id[:8],
        meta_description="Apply for a remote job today." + "x" * 80,
        focus_keyword="remote work",
        body_html=(
            f'<p>Great opportunity. <a href="{lead.apply_url}">Apply here</a></p>'
            + "x" * 900
        ),
        excerpt="Short excerpt.",
    )


@pytest.fixture
def make_direct_lead(make_lead):
    """Lead whose apply_url is a known ATS direct domain."""
    return make_lead(company="ATS Corp", title="Direct ATS Engineer", apply_url="https://boards.greenhouse.io/acme/jobs/1", verified=True)


@pytest.fixture
def make_aggregator_lead(make_lead):
    """Lead whose apply_url is a job-board aggregator."""
    return make_lead(company="WWR Inc", title="Aggregator Support Rep", apply_url="https://weworkremotely.com/remote-jobs/view/42", verified=True)


@pytest.fixture
def fake_store(tmp_path):
    store = PostedStore(tmp_path / "test.db")
    yield store
    store.close()


def _pipeline(
    leads: list[Lead],
    store: PostedStore,
    *,
    allow_aggregator_autopublish: bool = False,
) -> Pipeline:
    generator = MagicMock()
    source = _StaticLeadSource(leads)
    return Pipeline(
        source=source,
        generator=generator,
        store=store,
        wordpress=None,
        allow_aggregator_autopublish=allow_aggregator_autopublish,
    )


# ---------------------------------------------------------------------------
# Lane filter -- direct lane
# ---------------------------------------------------------------------------


def test_direct_lane_processes_only_direct_leads(
    make_direct_lead, make_aggregator_lead, fake_store
) -> None:
    pipeline = _pipeline([make_direct_lead, make_aggregator_lead], fake_store)
    with patch.object(pipeline._generator, "generate", side_effect=_fake_generate):
        report = pipeline.run(dry_run=True, lane="direct")

    labels = [r.label for r in report.results]
    direct_label = f"{make_direct_lead.company} — {make_direct_lead.title}"
    agg_label = f"{make_aggregator_lead.company} — {make_aggregator_lead.title}"
    assert direct_label in labels
    assert agg_label not in labels


def test_aggregator_lane_processes_only_aggregator_leads(
    make_direct_lead, make_aggregator_lead, fake_store
) -> None:
    pipeline = _pipeline([make_direct_lead, make_aggregator_lead], fake_store)
    with patch.object(pipeline._generator, "generate", side_effect=_fake_generate):
        report = pipeline.run(dry_run=True, lane="aggregator")

    labels = [r.label for r in report.results]
    direct_label = f"{make_direct_lead.company} — {make_direct_lead.title}"
    agg_label = f"{make_aggregator_lead.company} — {make_aggregator_lead.title}"
    assert agg_label in labels
    assert direct_label not in labels


def test_all_lane_processes_all_leads(
    make_direct_lead, make_aggregator_lead, fake_store
) -> None:
    pipeline = _pipeline([make_direct_lead, make_aggregator_lead], fake_store)
    with patch.object(pipeline._generator, "generate", side_effect=_fake_generate):
        report = pipeline.run(dry_run=True, lane="all")

    non_skip = [r for r in report.results if r.action != "skipped_already_posted"]
    assert len(non_skip) == 2


# ---------------------------------------------------------------------------
# Aggregator autopublish policy
# ---------------------------------------------------------------------------


def test_aggregator_lead_forced_to_draft_when_autopublish_off(
    make_aggregator_lead, fake_store, caplog
) -> None:
    pipeline = _pipeline([make_aggregator_lead], fake_store, allow_aggregator_autopublish=False)
    import logging
    with patch.object(pipeline._generator, "generate", side_effect=_fake_generate):
        with caplog.at_level(logging.INFO):
            report = pipeline.run(dry_run=True, lane="aggregator", status="publish")

    # Generator was called (lead passed the lane filter)
    processed = [r for r in report.results if r.action in ("dry_run", "skipped_qc")]
    assert len(processed) == 1
    # Autopublish guard log should appear
    assert "AGGREGATOR: forcing draft" in caplog.text


def test_aggregator_lead_allowed_when_autopublish_on(
    make_aggregator_lead, fake_store, caplog
) -> None:
    """When ALLOW_AGGREGATOR_AUTOPUBLISH=True no downgrade log should appear."""
    pipeline = _pipeline([make_aggregator_lead], fake_store, allow_aggregator_autopublish=True)
    import logging
    with patch.object(pipeline._generator, "generate", side_effect=_fake_generate):
        with caplog.at_level(logging.INFO):
            report = pipeline.run(dry_run=True, lane="aggregator")

    assert "needs manual direct-link review" not in caplog.text


# ---------------------------------------------------------------------------
# Dedup still works across lanes
# ---------------------------------------------------------------------------


def test_already_posted_lead_skipped_regardless_of_lane(
    make_direct_lead, fake_store
) -> None:
    fake_store.record(make_direct_lead.id, None, "")
    pipeline = _pipeline([make_direct_lead], fake_store)
    report = pipeline.run(dry_run=True, lane="direct")
    assert report.results[0].action == "skipped_already_posted"
