"""Tests for pledge-enforced apply-link framing in generation/prompts.py."""
from __future__ import annotations

import pytest

from wfh_pipeline.generation.prompts import apply_anchor_html, build_user_prompt


# ---------------------------------------------------------------------------
# apply_anchor_html — deterministic markup based on is_direct
# ---------------------------------------------------------------------------


def test_direct_lead_gets_direct_anchor_text(make_lead) -> None:
    lead = make_lead(apply_url="https://boards.greenhouse.io/acme/jobs/999")
    anchor = apply_anchor_html(lead)
    assert lead.is_direct is True
    assert "Apply directly on their site" in anchor
    assert lead.apply_url in anchor


def test_aggregator_lead_gets_view_listing_anchor(make_lead) -> None:
    lead = make_lead(apply_url="https://weworkremotely.com/remote-jobs/view/123")
    anchor = apply_anchor_html(lead)
    assert lead.is_direct is False
    assert "Apply directly" not in anchor
    assert "View this listing" in anchor
    assert lead.apply_url in anchor


def test_unknown_url_gets_view_listing_anchor(make_lead) -> None:
    """URLs we can't classify should fall into the non-direct branch."""
    lead = make_lead(apply_url="https://example.com/jobs/engineer")
    anchor = apply_anchor_html(lead)
    assert lead.is_direct is False
    assert "Apply directly" not in anchor
    assert "View this listing" in anchor


def test_anchor_always_contains_apply_url(make_lead) -> None:
    for url in [
        "https://boards.greenhouse.io/stripe/jobs/1",
        "https://remoteok.com/remote-jobs/99",
        "https://example.com/careers/1",
    ]:
        lead = make_lead(apply_url=url)
        assert url in apply_anchor_html(lead)


def test_anchor_has_wfh_apply_button_class(make_lead) -> None:
    for url in [
        "https://jobs.lever.co/openai/abc",
        "https://www.linkedin.com/jobs/view/123",
    ]:
        lead = make_lead(apply_url=url)
        assert 'class="wfh-apply-button"' in apply_anchor_html(lead)


def test_anchor_has_nofollow_noopener(make_lead) -> None:
    lead = make_lead(apply_url="https://apply.workable.com/acme/j/XYZ")
    anchor = apply_anchor_html(lead)
    assert 'rel="nofollow noopener"' in anchor
    assert 'target="_blank"' in anchor


# ---------------------------------------------------------------------------
# Known board names appear in link text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, board_name",
    [
        ("https://weworkremotely.com/remote-jobs/view/1", "We Work Remotely"),
        ("https://remoteok.com/remote-jobs/1", "RemoteOK"),
        ("https://www.linkedin.com/jobs/view/1", "LinkedIn"),
        ("https://www.indeed.com/viewjob?jk=abc", "Indeed"),
        ("https://remotive.com/remote-jobs/1", "Remotive"),
        ("https://himalayas.app/jobs/acme/role", "Himalayas"),
        ("https://www.flexjobs.com/jobs/1", "FlexJobs"),
    ],
)
def test_board_name_in_anchor_text(make_lead, url: str, board_name: str) -> None:
    lead = make_lead(apply_url=url)
    anchor = apply_anchor_html(lead)
    assert board_name in anchor


# ---------------------------------------------------------------------------
# build_user_prompt injects correct LINK FRAMING section
# ---------------------------------------------------------------------------


def test_user_prompt_direct_contains_apply_directly(make_lead) -> None:
    lead = make_lead(apply_url="https://boards.greenhouse.io/acme/jobs/1")
    prompt = build_user_prompt(lead)
    assert lead.is_direct is True
    assert "apply directly" in prompt.lower()
    assert "do NOT say" not in prompt


def test_user_prompt_aggregator_forbids_direct_apply_phrase(make_lead) -> None:
    lead = make_lead(apply_url="https://weworkremotely.com/remote-jobs/view/99")
    prompt = build_user_prompt(lead)
    assert lead.is_direct is False
    assert "do NOT say 'apply directly'" in prompt


def test_user_prompt_anchor_matches_apply_anchor_html(make_lead) -> None:
    """The anchor embedded in the user prompt must exactly match apply_anchor_html."""
    for url in [
        "https://boards.greenhouse.io/anthropic/jobs/1",
        "https://remoteok.com/remote-jobs/1",
    ]:
        lead = make_lead(apply_url=url)
        expected_anchor = apply_anchor_html(lead)
        prompt = build_user_prompt(lead)
        assert expected_anchor in prompt
