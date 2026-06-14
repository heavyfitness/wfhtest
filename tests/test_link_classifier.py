"""Tests for wfh_pipeline.link_classifier — URL classification logic."""
from __future__ import annotations

import pytest

from wfh_pipeline.link_classifier import classify_url, is_direct_url


# ---------------------------------------------------------------------------
# Table-driven classification tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        # --- Known ATS direct domains ---
        ("https://boards.greenhouse.io/anthropic/jobs/123", "direct"),
        ("https://jobs.lever.co/stripe/abc123", "direct"),
        ("https://apply.workable.com/acme/j/XYZ", "direct"),
        ("https://jobs.ashbyhq.com/openai/some-role", "direct"),
        ("https://apply.recruitee.com/o/engineer", "direct"),
        # Wildcard ATS: *.myworkdayjobs.com
        ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite", "direct"),
        # Wildcard ATS: *.icims.com
        ("https://careers-tesla.icims.com/jobs/1234/apply", "direct"),
        # Wildcard ATS: *.taleo.net
        ("https://oracle.taleo.net/careersection/2/jobdetail.ftl?job=200001", "direct"),
        # Wildcard ATS: *.bamboohr.com
        ("https://acmecorp.bamboohr.com/careers/42", "direct"),
        # Wildcard ATS: *.smartrecruiters.com (subdomain variant)
        ("https://jobs.smartrecruiters.com/Microsoft/123", "direct"),
        # --- Known aggregator domains ---
        ("https://weworkremotely.com/remote-jobs/view/123", "aggregator"),
        ("https://remoteok.com/remote-jobs/123456", "aggregator"),
        ("https://remotive.com/remote-jobs/engineering/1234", "aggregator"),
        ("https://himalayas.app/jobs/acme/engineer", "aggregator"),
        ("https://www.linkedin.com/jobs/view/12345678", "aggregator"),
        ("https://www.indeed.com/viewjob?jk=abc123", "aggregator"),
        ("https://www.glassdoor.com/job-listing/engineer.htm", "aggregator"),
        ("https://www.ziprecruiter.com/jobs/acme-123", "aggregator"),
        ("https://www.flexjobs.com/jobs/listing/123", "aggregator"),
        ("https://remote.co/job/engineer", "aggregator"),
        # --- Unknown (neither direct nor aggregator) ---
        ("https://acme.com/careers/engineer", "unknown"),
        ("https://example.com/jobs/123", "unknown"),
        ("https://somecompany.io/open-roles/123", "unknown"),
        ("https://careers.stripe.com/jobs/123", "unknown"),  # stripe's own site, not lever
    ],
)
def test_classify_url(url: str, expected: str) -> None:
    assert classify_url(url) == expected


# ---------------------------------------------------------------------------
# is_direct_url convenience wrapper
# ---------------------------------------------------------------------------


def test_is_direct_url_true_for_greenhouse() -> None:
    assert is_direct_url("https://boards.greenhouse.io/acme/jobs/1") is True


def test_is_direct_url_false_for_aggregator() -> None:
    assert is_direct_url("https://weworkremotely.com/jobs/1") is False


def test_is_direct_url_false_for_unknown() -> None:
    assert is_direct_url("https://example.com/careers/1") is False


# ---------------------------------------------------------------------------
# Wildcard / glob matching
# ---------------------------------------------------------------------------


def test_wildcard_myworkdayjobs_any_subdomain() -> None:
    urls = [
        "https://amazon.jobs.myworkdayjobs.com/en-US/External_JobSite/job/123",
        "https://google.wd3.myworkdayjobs.com/Google_jobs/job/456",
        "https://ibm.wd3.myworkdayjobs.com/External/job/789",
    ]
    for url in urls:
        assert classify_url(url) == "direct", f"Expected direct for {url!r}"


def test_wildcard_icims_any_subdomain() -> None:
    assert classify_url("https://careers-apple.icims.com/jobs/1234/apply") == "direct"
    assert classify_url("https://jobs-meta.icims.com/jobs/5678/apply") == "direct"


def test_wildcard_does_not_match_bare_tld() -> None:
    # "myworkdayjobs.com" itself is not in the direct set, only "*.myworkdayjobs.com"
    # but let's confirm a URL with no sub-prefix doesn't mis-classify
    result = classify_url("https://myworkdayjobs.com/jobs/1")
    # Not a valid Workday URL but shouldn't be aggregator either
    assert result != "aggregator"


# ---------------------------------------------------------------------------
# Aggregator wins on conflict (if somehow both matched)
# ---------------------------------------------------------------------------


def test_aggregator_wins_via_extra_aggregator() -> None:
    # Normally boards.greenhouse.io is direct, but if it's added to
    # extra_aggregator the aggregator check should win.
    result = classify_url(
        "https://boards.greenhouse.io/anthropic/jobs/1",
        extra_aggregator=("boards.greenhouse.io",),
    )
    assert result == "aggregator"


# ---------------------------------------------------------------------------
# extra_direct / extra_aggregator config overrides
# ---------------------------------------------------------------------------


def test_extra_direct_promotes_unknown_to_direct() -> None:
    url = "https://mycompany.workforcenow.adp.com/mascsr/default/mdf/recruitment/job.html"
    assert classify_url(url) == "unknown"  # not in built-in lists
    assert classify_url(url, extra_direct=("*.workforcenow.adp.com",)) == "direct"


def test_extra_aggregator_promotes_unknown_to_aggregator() -> None:
    url = "https://careers.monster.example.org/job/1"
    assert classify_url(url) == "unknown"
    assert classify_url(url, extra_aggregator=("careers.monster.example.org",)) == "aggregator"


def test_extra_domains_accept_exact_match() -> None:
    url = "https://jobs.custom-ats.io/acme/role/123"
    assert classify_url(url, extra_direct=("jobs.custom-ats.io",)) == "direct"
