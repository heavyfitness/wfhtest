"""Tests for the QC backstop that rejects direct-apply framing on aggregator leads."""
from __future__ import annotations

import pytest

from wfh_pipeline.models import GeneratedPost, Lead
from wfh_pipeline.qc import validate_post

# A body long enough to pass the MIN_BODY_CHARS check.
_PADDING = "x" * 900


def _post(body_html: str, apply_url: str) -> tuple[Lead, GeneratedPost]:
    """Build a (lead, post) pair for QC testing."""
    # We import Lead here so we pick up the conftest fixture via make_lead when needed,
    # but for parametrized tests we build directly.
    lead = Lead(
        company="Acme",
        title="Remote Engineer",
        apply_url=apply_url,
        verified=True,
        source="test",
    )
    post = GeneratedPost(
        seo_title="Remote Engineer at Acme",
        slug="remote-engineer-acme",
        meta_description="Apply for a remote engineering role at Acme today.",
        focus_keyword="remote engineer jobs",
        body_html=body_html,
        excerpt="Acme is hiring a remote engineer.",
    )
    return lead, post


# ---------------------------------------------------------------------------
# Aggregator lead + "apply directly" framing → QC rejects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "Apply directly",
        "apply directly",
        "Apply directly on their site",
        "direct apply link",
        "apply directly to",
        "apply directly with",
    ],
)
def test_qc_rejects_direct_phrase_for_aggregator_lead(phrase: str) -> None:
    url = "https://weworkremotely.com/remote-jobs/view/123"
    body = (
        f'<p>{phrase} — <a href="{url}">click here</a></p>'
        + _PADDING
    )
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    assert any("direct-apply framing" in p for p in problems), (
        f"Expected a direct-apply framing problem for phrase {phrase!r}, got: {problems}"
    )


def test_qc_rejects_direct_phrase_for_unknown_lead() -> None:
    """Unknown URL (not classified as direct) should also trigger the backstop."""
    url = "https://example.com/careers/engineer"
    body = f'<p>Apply directly on their site. <a href="{url}">Apply</a></p>' + _PADDING
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    assert any("direct-apply framing" in p for p in problems)


# ---------------------------------------------------------------------------
# Direct lead + "apply directly" framing → QC passes
# ---------------------------------------------------------------------------


def test_qc_allows_direct_phrase_for_direct_ats_lead() -> None:
    url = "https://boards.greenhouse.io/anthropic/jobs/999"
    body = (
        f'<p>Apply directly on their site via <a href="{url}">Greenhouse</a></p>'
        + _PADDING
    )
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    # No direct-apply framing problem — the URL is classified as direct
    assert not any("direct-apply framing" in p for p in problems)


@pytest.mark.parametrize(
    "url",
    [
        "https://jobs.lever.co/stripe/abc",
        "https://apply.workable.com/acme/j/XYZ",
        "https://jobs.ashbyhq.com/openai/role",
        "https://careers-tesla.icims.com/jobs/1/apply",
    ],
)
def test_qc_allows_direct_phrase_for_known_ats_domains(url: str) -> None:
    body = (
        f'<p>Apply directly at <a href="{url}">their site</a>.</p>'
        + _PADDING
    )
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    assert not any("direct-apply framing" in p for p in problems)


# ---------------------------------------------------------------------------
# Aggregator lead with correct aggregator framing → QC passes
# ---------------------------------------------------------------------------


def test_qc_passes_correct_aggregator_framing() -> None:
    url = "https://remoteok.com/remote-jobs/1234"
    body = (
        f'<p>View this listing on RemoteOK. <a href="{url}">View this listing on RemoteOK</a></p>'
        + _PADDING
    )
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    # The apply URL is present and no direct-apply phrase is used
    no_framing_problem = not any("direct-apply framing" in p for p in problems)
    no_missing_url = not any("apply_url missing" in p for p in problems)
    assert no_framing_problem
    assert no_missing_url


# ---------------------------------------------------------------------------
# Other QC checks still work (regression — backstop doesn't mask them)
# ---------------------------------------------------------------------------


def test_qc_still_catches_missing_apply_url_for_aggregator_lead() -> None:
    url = "https://weworkremotely.com/remote-jobs/view/99"
    # Body doesn't include the apply URL at all
    body = "<p>This job is great. View the listing somewhere.</p>" + _PADDING
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    assert any("apply_url missing" in p for p in problems)


def test_qc_still_catches_script_tags() -> None:
    url = "https://weworkremotely.com/remote-jobs/view/99"
    body = (
        f'<p>View listing. <a href="{url}">View this listing</a></p>'
        + "<script>alert(1)</script>"
        + _PADDING
    )
    lead, post = _post(body, apply_url=url)
    problems = validate_post(lead, post)
    assert any("<script>" in p for p in problems)
