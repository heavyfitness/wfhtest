"""Tests for RSS, ATS, and multi-source lead sources.

All tests use recorded/mocked payloads — no live network calls.
"""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from wfh_pipeline.models import Lead
from wfh_pipeline.sources.greenhouse import ATSLeadSource, GreenhouseLeadSource
from wfh_pipeline.sources.job_api import JobAPILeadSource, _guess_category
from wfh_pipeline.sources.lever import LeverLeadSource
from wfh_pipeline.sources.multi import MultiLeadSource
from wfh_pipeline.sources.rss import (
    RemoteOKLeadSource,
    RemotiveLeadSource,
    WeWorkRemotelyLeadSource,
    _strip_html,
)


# ---------------------------------------------------------------------------
# Sample feed payloads (recorded; no live network)
# ---------------------------------------------------------------------------

_REMOTEOK_ENTRY = {
    "title": "Senior Python Engineer",
    "company": "Acme Corp",
    "link": "https://remoteok.com/remote-jobs/acme-python-engineer-123",
    "summary": "<p>We are building <strong>great things</strong>.</p>",
    "tags": [{"term": "python"}, {"term": "engineering"}],
    "published_parsed": type("T", (), {
        "tm_year": 2026, "tm_mon": 6, "tm_mday": 10
    })(),
}

_WWR_ENTRY = {
    "title": "Doist: iOS Developer",
    "link": "https://weworkremotely.com/remote-jobs/doist-ios-developer",
    "summary": "<p>Join our <em>distributed</em> team.</p>",
    "tags": [{"term": "Full-Stack Programming"}],
    "published_parsed": type("T", (), {
        "tm_year": 2026, "tm_mon": 6, "tm_mday": 11
    })(),
}

_REMOTIVE_JOB = {
    "id": 42,
    "title": "Data Analyst",
    "company_name": "DataCo",
    "url": "https://remotive.com/remote-jobs/data/analyst-42",
    "category": "Data",
    "tags": ["sql", "python"],
    "job_type": "full_time",
    "publication_date": "2026-06-09T00:00:00",
    "description": "<p>Analyse data.</p>",
    "company_logo_url": "https://example.com/logo.png",
}

_GH_JOB = {
    "id": 9001,
    "title": "Remote DevOps Engineer",
    "location": {"name": "Remote"},
    "content": "<p>We deploy things.</p>",
}

_LEVER_POSTING = {
    "text": "Staff Engineer",
    "hostedUrl": "https://jobs.lever.co/whereby/abc-123",
    "applyUrl": "https://jobs.lever.co/whereby/abc-123/apply",
    "categories": {
        "team": "Engineering",
        "location": "Remote",
        "commitment": "Full-time",
    },
    "tags": ["remote"],
    "createdAt": 1717200000000,  # approx 2024-06-01
    "descriptionPlain": "Come build distributed systems.",
}

_JSEARCH_JOB = {
    "job_title": "Full Stack Developer",
    "employer_name": "TechCorp",
    "job_apply_link": "https://boards.greenhouse.io/techcorp/jobs/111",
    "job_employment_type": "FULLTIME",
    "job_posted_at_timestamp": 1717200000,
    "job_description": "Build cool things.",
    "job_required_skills": ["React", "Node.js"],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_feedparser_result(entries):
    """Create a minimal feedparser result dict."""
    return {"entries": entries, "status": 200}


# ---------------------------------------------------------------------------
# strip_html helper
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags() -> None:
    assert _strip_html("<p><strong>Hello</strong> world</p>") == "Hello world"


def test_strip_html_unescapes_entities() -> None:
    assert _strip_html("Acme &amp; Co") == "Acme & Co"


def test_strip_html_empty() -> None:
    assert _strip_html("") == ""


# ---------------------------------------------------------------------------
# RemoteOKLeadSource
# ---------------------------------------------------------------------------


class TestRemoteOKLeadSource:
    def _source_with_entries(self, entries):
        src = RemoteOKLeadSource()
        with patch("wfh_pipeline.sources.rss.feedparser") as mock_fp:
            mock_fp.parse.return_value = _make_feedparser_result(entries)
            return src.fetch_new_leads()

    def test_parses_entry_to_lead(self) -> None:
        leads = self._source_with_entries([_REMOTEOK_ENTRY])
        assert len(leads) == 1
        lead = leads[0]
        assert lead.company == "Acme Corp"
        assert lead.title == "Senior Python Engineer"
        assert lead.apply_url == "https://remoteok.com/remote-jobs/acme-python-engineer-123"
        assert lead.source == "rss:remoteok"
        assert lead.source_trust == "aggregator"
        assert lead.remote is True
        assert lead.verified is True

    def test_classifies_as_aggregator(self) -> None:
        leads = self._source_with_entries([_REMOTEOK_ENTRY])
        assert leads[0].link_type == "aggregator"
        assert leads[0].is_direct is False

    def test_date_parsed_correctly(self) -> None:
        leads = self._source_with_entries([_REMOTEOK_ENTRY])
        assert leads[0].date_found == date(2026, 6, 10)

    def test_html_stripped_from_description(self) -> None:
        leads = self._source_with_entries([_REMOTEOK_ENTRY])
        assert "<p>" not in leads[0].description
        assert "great things" in leads[0].description

    def test_empty_feed_returns_empty_list(self) -> None:
        leads = self._source_with_entries([])
        assert leads == []

    def test_missing_company_skipped(self) -> None:
        entry = dict(_REMOTEOK_ENTRY, company="")
        leads = self._source_with_entries([entry])
        assert leads == []

    def test_missing_link_skipped(self) -> None:
        entry = dict(_REMOTEOK_ENTRY, link="")
        leads = self._source_with_entries([entry])
        assert leads == []

    def test_source_trust_is_aggregator(self) -> None:
        assert RemoteOKLeadSource.trust == "aggregator"


# ---------------------------------------------------------------------------
# WeWorkRemotelyLeadSource
# ---------------------------------------------------------------------------


class TestWeWorkRemotelyLeadSource:
    def _source_with_entries(self, entries):
        src = WeWorkRemotelyLeadSource()
        with patch("wfh_pipeline.sources.rss.feedparser") as mock_fp:
            mock_fp.parse.return_value = _make_feedparser_result(entries)
            return src.fetch_new_leads()

    def test_parses_company_title_from_colon_format(self) -> None:
        leads = self._source_with_entries([_WWR_ENTRY])
        assert len(leads) == 1
        lead = leads[0]
        assert lead.company == "Doist"
        assert lead.title == "iOS Developer"

    def test_classifies_as_aggregator(self) -> None:
        leads = self._source_with_entries([_WWR_ENTRY])
        assert leads[0].link_type == "aggregator"

    def test_no_colon_in_title_uses_full_title(self) -> None:
        entry = dict(_WWR_ENTRY, title="SomeJobNoColon")
        leads = self._source_with_entries([entry])
        assert leads[0].title == "SomeJobNoColon"
        assert leads[0].company == "Unknown"

    def test_source_trust_is_aggregator(self) -> None:
        assert WeWorkRemotelyLeadSource.trust == "aggregator"

    def test_missing_link_skipped(self) -> None:
        entry = dict(_WWR_ENTRY, link="")
        leads = self._source_with_entries([entry])
        assert leads == []


# ---------------------------------------------------------------------------
# RemotiveLeadSource (JSON API)
# ---------------------------------------------------------------------------


class TestRemotiveLeadSource:
    def _source_with_jobs(self, jobs):
        src = RemotiveLeadSource()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"jobs": jobs, "job-count": len(jobs)}
        mock_resp.raise_for_status = MagicMock()
        with patch("wfh_pipeline.sources.rss.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            return src.fetch_new_leads()

    def test_parses_job_to_lead(self) -> None:
        leads = self._source_with_jobs([_REMOTIVE_JOB])
        assert len(leads) == 1
        lead = leads[0]
        assert lead.company == "DataCo"
        assert lead.title == "Data Analyst"
        assert lead.apply_url == "https://remotive.com/remote-jobs/data/analyst-42"
        assert lead.source_trust == "aggregator"

    def test_classifies_as_aggregator(self) -> None:
        leads = self._source_with_jobs([_REMOTIVE_JOB])
        assert leads[0].link_type == "aggregator"

    def test_date_parsed_from_publication_date(self) -> None:
        leads = self._source_with_jobs([_REMOTIVE_JOB])
        assert leads[0].date_found == date(2026, 6, 9)

    def test_category_mapped(self) -> None:
        leads = self._source_with_jobs([_REMOTIVE_JOB])
        assert leads[0].category == "data"

    def test_empty_jobs_returns_empty_list(self) -> None:
        leads = self._source_with_jobs([])
        assert leads == []

    def test_http_error_returns_empty_list(self) -> None:
        import httpx
        src = RemotiveLeadSource()
        with patch("wfh_pipeline.sources.rss.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = httpx.HTTPError("timeout")
            leads = src.fetch_new_leads()
        assert leads == []


# ---------------------------------------------------------------------------
# GreenhouseLeadSource
# ---------------------------------------------------------------------------


class TestGreenhouseLeadSource:
    def _source_with_jobs(self, jobs, token="acme"):
        src = GreenhouseLeadSource(token)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": jobs}
        mock_resp.raise_for_status = MagicMock()
        with patch("wfh_pipeline.sources.greenhouse.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            return src.fetch_new_leads()

    def test_parses_job_to_direct_lead(self) -> None:
        leads = self._source_with_jobs([_GH_JOB])
        assert len(leads) == 1
        lead = leads[0]
        assert lead.title == "Remote DevOps Engineer"
        assert "acme" in lead.apply_url
        assert lead.source_trust == "direct"
        assert lead.verified is True

    def test_apply_url_is_greenhouse_board_link(self) -> None:
        leads = self._source_with_jobs([_GH_JOB])
        assert leads[0].apply_url.startswith("https://boards.greenhouse.io/acme/jobs/")

    def test_classifies_as_direct(self) -> None:
        leads = self._source_with_jobs([_GH_JOB])
        assert leads[0].link_type == "direct"
        assert leads[0].is_direct is True

    def test_non_remote_skipped_when_require_remote(self) -> None:
        job = dict(_GH_JOB, location={"name": "New York, NY"})
        leads = self._source_with_jobs([job])
        assert leads == []

    def test_non_remote_included_when_require_remote_off(self) -> None:
        src = GreenhouseLeadSource("acme", require_remote=False)
        job = dict(_GH_JOB, location={"name": "New York, NY"})
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": [job]}
        mock_resp.raise_for_status = MagicMock()
        with patch("wfh_pipeline.sources.greenhouse.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            leads = src.fetch_new_leads()
        assert len(leads) == 1

    def test_http_error_returns_empty_list(self) -> None:
        import httpx as httpx_lib
        src = GreenhouseLeadSource("bad-token")
        with patch("wfh_pipeline.sources.greenhouse.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.side_effect = httpx_lib.HTTPError("not found")
            leads = src.fetch_new_leads()
        assert leads == []

    def test_ats_trust_is_direct(self) -> None:
        assert ATSLeadSource.trust == "direct"
        assert GreenhouseLeadSource.trust == "direct"

    def test_keyword_filter_skips_non_matching(self) -> None:
        src = GreenhouseLeadSource("acme", keyword_filters=["frontend"])
        # _GH_JOB title is "Remote DevOps Engineer" — no "frontend"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"jobs": [_GH_JOB]}
        mock_resp.raise_for_status = MagicMock()
        with patch("wfh_pipeline.sources.greenhouse.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            leads = src.fetch_new_leads()
        assert leads == []


# ---------------------------------------------------------------------------
# LeverLeadSource
# ---------------------------------------------------------------------------


class TestLeverLeadSource:
    def _source_with_postings(self, postings, status=200, slug="whereby"):
        src = LeverLeadSource(slug)
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.json.return_value = postings
        with patch("wfh_pipeline.sources.lever.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.get.return_value = mock_resp
            return src.fetch_new_leads()

    def test_parses_posting_to_direct_lead(self) -> None:
        leads = self._source_with_postings([_LEVER_POSTING])
        assert len(leads) == 1
        lead = leads[0]
        assert lead.title == "Staff Engineer"
        assert lead.apply_url == "https://jobs.lever.co/whereby/abc-123"
        assert lead.source_trust == "direct"

    def test_classifies_as_direct(self) -> None:
        leads = self._source_with_postings([_LEVER_POSTING])
        assert leads[0].link_type == "direct"
        assert leads[0].is_direct is True

    def test_404_returns_empty_list(self) -> None:
        leads = self._source_with_postings([], status=404)
        assert leads == []

    def test_non_remote_posting_skipped(self) -> None:
        posting = dict(_LEVER_POSTING)
        posting["categories"] = {"team": "Engineering", "location": "New York", "commitment": "Full-time"}
        posting["tags"] = []
        leads = self._source_with_postings([posting])
        assert leads == []

    def test_lever_trust_is_direct(self) -> None:
        assert LeverLeadSource.trust == "direct"


# ---------------------------------------------------------------------------
# MultiLeadSource
# ---------------------------------------------------------------------------


class TestMultiLeadSource:
    def _make_static_source(self, leads: list[Lead], name: str = "test") -> "Any":
        from wfh_pipeline.sources.base import LeadSource

        class _Static(LeadSource):
            trust = "unknown"

            def fetch_new_leads(self) -> list[Lead]:
                return leads

        src = _Static()
        src.name = name
        return src

    def _make_lead(self, url: str, company: str = "Co") -> Lead:
        return Lead(company=company, title="Role", apply_url=url, verified=True)

    def test_combines_leads_from_multiple_sources(self) -> None:
        lead_a = self._make_lead("https://boards.greenhouse.io/acme/jobs/1", "A")
        lead_b = self._make_lead("https://weworkremotely.com/jobs/2", "B")
        src = MultiLeadSource([
            self._make_static_source([lead_a], "src1"),
            self._make_static_source([lead_b], "src2"),
        ])
        leads = src.fetch_new_leads()
        assert len(leads) == 2
        companies = {l.company for l in leads}
        assert companies == {"A", "B"}

    def test_deduplicates_same_url_across_sources(self) -> None:
        url = "https://boards.greenhouse.io/acme/jobs/1"
        lead_a = self._make_lead(url, "A")
        lead_b = self._make_lead(url, "B")  # same URL -> same id
        src = MultiLeadSource([
            self._make_static_source([lead_a], "src1"),
            self._make_static_source([lead_b], "src2"),
        ])
        leads = src.fetch_new_leads()
        assert len(leads) == 1

    def test_source_error_is_caught_not_propagated(self) -> None:
        class _BrokenSource:
            name = "broken"

            def fetch_new_leads(self):
                raise RuntimeError("boom")

        lead = self._make_lead("https://boards.greenhouse.io/acme/jobs/9")
        src = MultiLeadSource([
            _BrokenSource(),  # type: ignore[arg-type]
            self._make_static_source([lead], "ok"),
        ])
        leads = src.fetch_new_leads()
        assert len(leads) == 1  # broken source skipped, ok source returned

    def test_empty_sources_returns_empty_list(self) -> None:
        src = MultiLeadSource([])
        assert src.fetch_new_leads() == []

    def test_sources_property_returns_copy(self) -> None:
        child = self._make_static_source([], "x")
        src = MultiLeadSource([child])
        assert src.sources == [child]
        src.sources.append(child)  # modifying copy should not affect original
        assert len(src.sources) == 1


# ---------------------------------------------------------------------------
# Lane classification sanity: RSS=aggregator, ATS=direct
# ---------------------------------------------------------------------------


def test_remoteok_leads_enter_aggregator_lane() -> None:
    lead = Lead(
        company="Acme",
        title="Engineer",
        apply_url="https://remoteok.com/remote-jobs/acme-engineer-999",
        source_trust="aggregator",
        verified=True,
    )
    assert lead.link_type == "aggregator"
    assert lead.is_direct is False


def test_greenhouse_leads_enter_direct_lane() -> None:
    lead = Lead(
        company="Stripe",
        title="SRE",
        apply_url="https://boards.greenhouse.io/stripe/jobs/77",
        source_trust="direct",
        verified=True,
    )
    assert lead.link_type == "direct"
    assert lead.is_direct is True


def test_lever_leads_enter_direct_lane() -> None:
    lead = Lead(
        company="Whereby",
        title="Staff Engineer",
        apply_url="https://jobs.lever.co/whereby/abc-123",
        source_trust="direct",
        verified=True,
    )
    assert lead.link_type == "direct"
    assert lead.is_direct is True


def test_wwr_leads_enter_aggregator_lane() -> None:
    lead = Lead(
        company="Doist",
        title="iOS Developer",
        apply_url="https://weworkremotely.com/remote-jobs/doist-ios-developer",
        source_trust="aggregator",
        verified=True,
    )
    assert lead.link_type == "aggregator"
    assert lead.is_direct is False


# ---------------------------------------------------------------------------
# JobAPILeadSource category inference
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, skills, expected",
    [
        ("Senior Software Engineer", ["Python", "Django"], "engineering"),
        ("UX Designer", [], "design"),
        ("Content Marketing Manager", [], "marketing"),
        ("Account Executive", [], "sales"),
        ("Customer Success Manager", [], "customer-success"),
        ("Product Manager", [], "product"),
        ("Data Scientist", ["SQL"], "data"),
        ("CFO", [], "finance"),
        ("Random Job Title", [], "remote-jobs"),
    ],
)
def test_guess_category(title: str, skills: list, expected: str) -> None:
    assert _guess_category(title, skills) == expected


def test_job_api_parses_direct_apply_link() -> None:
    src = JobAPILeadSource("fake-key")
    lead = src._job_to_lead(_JSEARCH_JOB)
    assert lead is not None
    assert lead.company == "TechCorp"
    assert lead.title == "Full Stack Developer"
    assert lead.apply_url == "https://boards.greenhouse.io/techcorp/jobs/111"
    # JSearch returned a Greenhouse URL -> direct
    assert lead.link_type == "direct"


def test_job_api_skips_entry_without_url() -> None:
    src = JobAPILeadSource("fake-key")
    job = dict(_JSEARCH_JOB, job_apply_link="", job_google_link="")
    assert src._job_to_lead(job) is None


def test_job_api_skips_relative_url() -> None:
    src = JobAPILeadSource("fake-key")
    job = dict(_JSEARCH_JOB, job_apply_link="/relative/path")
    assert src._job_to_lead(job) is None
