"""Tests for RelevanceFilter and multi-query JobAPILeadSource."""
from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from wfh_pipeline.models import Lead
from wfh_pipeline.relevance import RelevanceFilter, load_relevance_config


# ---------------------------------------------------------------------------
# RelevanceFilter unit tests
# ---------------------------------------------------------------------------


def _lead(title: str, description: str = "") -> Lead:
    return Lead(
        company="Acme",
        title=title,
        apply_url="https://boards.greenhouse.io/acme/jobs/123",
        source="test",
        source_trust="direct",
        description=description,
        date_found=date.today(),
    )


class TestRelevanceFilterKeep:
    """Leads that should pass the default filter."""

    def test_customer_service_rep(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Remote Customer Service Representative")

    def test_data_entry_clerk(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Data Entry Clerk — Work From Home")

    def test_chat_support_agent(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Chat Support Agent (Remote)")

    def test_virtual_assistant(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Virtual Assistant — Remote")

    def test_entry_level_in_description(self):
        rf = RelevanceFilter.from_env()
        # Title alone has no keywords, but description does
        assert rf.is_relevant("Team Member", "Entry level role, no experience needed")

    def test_customer_care_associate(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Customer Care Associate — Remote")

    def test_help_desk_agent(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Help Desk Agent (WFH)")

    def test_support_specialist(self):
        rf = RelevanceFilter.from_env()
        assert rf.is_relevant("Support Specialist — Customer Experience")


class TestRelevanceFilterReject:
    """Leads that should be rejected by the default filter."""

    def test_senior_engineer(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Senior Software Engineer")

    def test_staff_engineer(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Staff Software Engineer — Platform")

    def test_principal_engineer(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Principal Engineer, Infrastructure")

    def test_director(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Director of Engineering")

    def test_vp(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("VP of Product")

    def test_manager(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Engineering Manager, Growth")

    def test_talent_community_placeholder(self):
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Talent Community — Join Our Team")

    def test_no_include_match_strict_mode(self):
        """A title with no CS/entry-level keyword is rejected in strict (default) mode."""
        rf = RelevanceFilter.from_env()
        assert not rf.is_relevant("Software Developer (Remote)")

    def test_security_engineer(self):
        rf = RelevanceFilter.from_env()
        # "engineer" is in exclude list
        assert not rf.is_relevant("Security Engineer")


class TestRelevanceFilterCustom:
    """Custom keyword configuration."""

    def test_custom_include_passes(self):
        rf = RelevanceFilter(include_keywords=("billing",), exclude_title_keywords=())
        assert rf.is_relevant("Remote Billing Coordinator")

    def test_custom_include_fails_when_missing(self):
        rf = RelevanceFilter(include_keywords=("billing",), exclude_title_keywords=())
        assert not rf.is_relevant("Remote Customer Service Rep")

    def test_custom_exclude_blocks(self):
        rf = RelevanceFilter(
            include_keywords=("customer service",),
            exclude_title_keywords=("contractor",),
        )
        assert not rf.is_relevant("Customer Service Contractor")

    def test_permissive_mode_no_include_needed(self):
        rf = RelevanceFilter(
            include_keywords=("customer service",),
            exclude_title_keywords=(),
            permissive=True,
        )
        # No include keyword match — still kept because permissive=True
        assert rf.is_relevant("Software Developer (Remote)")

    def test_permissive_still_applies_exclude(self):
        rf = RelevanceFilter(
            include_keywords=(),
            exclude_title_keywords=("senior",),
            permissive=True,
        )
        assert not rf.is_relevant("Senior Software Engineer")

    def test_empty_keywords_pass_all(self):
        rf = RelevanceFilter(
            include_keywords=(), exclude_title_keywords=(), permissive=False
        )
        assert rf.is_relevant("Anything Goes Here")


class TestFilterLeads:
    """filter_leads() bulk API."""

    def test_filters_list(self):
        rf = RelevanceFilter.from_env()
        leads = [
            _lead("Remote Customer Service Rep"),
            _lead("Senior Software Engineer"),
            _lead("Data Entry Specialist — WFH"),
            _lead("Staff Engineer, Platform"),
        ]
        kept = rf.filter_leads(leads)
        titles = [l.title for l in kept]
        assert "Remote Customer Service Rep" in titles
        assert "Data Entry Specialist — WFH" in titles
        assert "Senior Software Engineer" not in titles
        assert "Staff Engineer, Platform" not in titles
        assert len(kept) == 2

    def test_empty_list_ok(self):
        rf = RelevanceFilter.from_env()
        assert rf.filter_leads([]) == []


class TestLoadRelevanceConfig:
    """load_relevance_config() reads env vars."""

    def test_defaults_when_no_env(self, monkeypatch):
        monkeypatch.delenv("RELEVANCE_INCLUDE_KEYWORDS", raising=False)
        monkeypatch.delenv("RELEVANCE_EXCLUDE_TITLE", raising=False)
        monkeypatch.delenv("RELEVANCE_PERMISSIVE", raising=False)
        include, exclude, permissive = load_relevance_config()
        assert "customer service" in include
        assert "senior" in exclude
        assert permissive is False

    def test_custom_env_include(self, monkeypatch):
        monkeypatch.setenv("RELEVANCE_INCLUDE_KEYWORDS", "billing,collections")
        include, _, _ = load_relevance_config()
        assert include == ("billing", "collections")

    def test_custom_env_exclude(self, monkeypatch):
        monkeypatch.setenv("RELEVANCE_EXCLUDE_TITLE", "executive,vp")
        _, exclude, _ = load_relevance_config()
        assert exclude == ("executive", "vp")

    def test_permissive_true(self, monkeypatch):
        monkeypatch.setenv("RELEVANCE_PERMISSIVE", "true")
        _, _, permissive = load_relevance_config()
        assert permissive is True

    def test_empty_include_means_pass_all(self, monkeypatch):
        monkeypatch.setenv("RELEVANCE_INCLUDE_KEYWORDS", "")
        include, _, _ = load_relevance_config()
        # Empty string → uses defaults (not empty tuple)
        assert len(include) > 0


# ---------------------------------------------------------------------------
# JobAPILeadSource multi-query tests
# ---------------------------------------------------------------------------


def _make_jsearch_response(jobs: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": jobs, "status": "OK"}
    resp.raise_for_status.return_value = None
    return resp


def _sample_job(job_id: str, title: str, company: str = "ACME Corp") -> dict:
    return {
        "job_id": job_id,
        "job_title": title,
        "employer_name": company,
        "job_apply_link": f"https://boards.greenhouse.io/acme/jobs/{job_id}",
        "job_employment_type": "FULLTIME",
        "job_posted_at_timestamp": None,
        "job_description": f"Remote {title} role.",
        "job_required_skills": [],
    }


class TestJobAPILeadSourceMultiQuery:
    """Multi-query mode: combines results, deduplicates by job_id."""

    def test_single_query_compat(self):
        """Backwards-compatible single-query mode still works."""
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        source = JobAPILeadSource("key", query="remote customer service", max_results=5)
        assert source._queries == ["remote customer service"]
        assert source._max_per_query == 5

    def test_multi_query_list(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        source = JobAPILeadSource(
            "key",
            queries=["remote CS", "remote data entry"],
            max_results_per_query=10,
        )
        assert source._queries == ["remote CS", "remote data entry"]
        assert source._max_per_query == 10

    def test_multi_query_fetch_combines_results(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        jobs_q1 = [_sample_job("j1", "Customer Service Rep")]
        jobs_q2 = [_sample_job("j2", "Data Entry Clerk")]

        # With max_results_per_query=10, pages=ceil(10/10)=1 per query.
        # So each query makes exactly ONE request.
        responses = [
            _make_jsearch_response(jobs_q1),  # q1 page 1
            _make_jsearch_response(jobs_q2),  # q2 page 1
        ]

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = responses
            mock_client_cls.return_value = mock_client

            source = JobAPILeadSource(
                "key",
                queries=["remote CS", "remote data entry"],
                max_results_per_query=10,
            )
            leads = source.fetch_new_leads()

        titles = [l.title for l in leads]
        assert "Customer Service Rep" in titles
        assert "Data Entry Clerk" in titles
        assert len(leads) == 2

    def test_multi_query_deduplicates_same_job_id(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        # Same job_id returned by both queries
        job = _sample_job("dup123", "Customer Rep")
        responses = [
            _make_jsearch_response([job]),
            _make_jsearch_response([]),
            _make_jsearch_response([job]),  # duplicate
            _make_jsearch_response([]),
        ]

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = responses
            mock_client_cls.return_value = mock_client

            source = JobAPILeadSource(
                "key",
                queries=["q1", "q2"],
                max_results_per_query=10,
            )
            leads = source.fetch_new_leads()

        assert len(leads) == 1, "Duplicate job_id should be deduped across queries"

    def test_403_stops_query_gracefully(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource
        import httpx

        resp_403 = MagicMock()
        resp_403.status_code = 403
        resp_403.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=resp_403
        )

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.return_value = resp_403
            mock_client_cls.return_value = mock_client

            source = JobAPILeadSource("bad_key", query="remote CS", max_results=5)
            leads = source.fetch_new_leads()

        assert leads == [], "403 should return empty list, not raise"

    def test_missing_fields_skipped(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        bad_job = {"job_id": "x99", "job_title": "", "employer_name": "Acme",
                   "job_apply_link": "https://example.com/job/1"}  # no title

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.get.side_effect = [
                _make_jsearch_response([bad_job]),
                _make_jsearch_response([]),
            ]
            mock_client_cls.return_value = mock_client

            source = JobAPILeadSource("key", query="test", max_results=5)
            leads = source.fetch_new_leads()

        assert leads == [], "Jobs with empty title should be skipped"


# ---------------------------------------------------------------------------
# Pipeline + RelevanceFilter integration
# ---------------------------------------------------------------------------


def test_pipeline_applies_relevance_filter(tmp_path):
    """Pipeline skips leads that fail the relevance filter."""
    from unittest.mock import MagicMock

    from wfh_pipeline.pipeline import Pipeline

    cs_lead = Lead(
        company="ACME", title="Remote Customer Service Rep",
        apply_url="https://boards.greenhouse.io/acme/jobs/1",
        source="test", source_trust="direct", date_found=date.today(),
        verified=True,
    )
    eng_lead = Lead(
        company="ACME", title="Senior Software Engineer",
        apply_url="https://boards.greenhouse.io/acme/jobs/2",
        source="test", source_trust="direct", date_found=date.today(),
        verified=True,
    )

    source = MagicMock()
    source.fetch_new_leads.return_value = [cs_lead, eng_lead]

    generator = MagicMock()
    fake_post = MagicMock()
    fake_post.seo_title = "Customer Service Job"
    fake_post.slug = "customer-service-job"
    fake_post.focus_keyword = "customer service"
    fake_post.excerpt = "Apply now."
    fake_post.body_html = "<p>Great job.</p>"
    fake_post.meta_description = "CS job."
    generator.generate.return_value = fake_post

    store = MagicMock()
    store.is_posted.return_value = False

    qc_patch = patch("wfh_pipeline.pipeline.validate_post", return_value=[])
    schema_patch = patch(
        "wfh_pipeline.pipeline.append_jobposting_schema",
        side_effect=lambda html, _: html,
    )

    rf = RelevanceFilter.from_env()
    pipeline = Pipeline(
        source=source,
        generator=generator,
        store=store,
        relevance_filter=rf,
    )

    with qc_patch, schema_patch:
        report = pipeline.run(dry_run=True, lane="all")

    actions = [(r.label, r.action) for r in report.results]
    # CS lead should be dry_run; engineer lead should not appear (filtered out)
    labels = [r.label for r in report.results]
    assert any("Customer Service" in l for l in labels)
    assert not any("Senior Software Engineer" in l for l in labels)


def test_pipeline_no_relevance_filter_passes_all(tmp_path):
    """With relevance_filter=None, all leads reach generation."""
    from unittest.mock import MagicMock

    from wfh_pipeline.pipeline import Pipeline

    eng_lead = Lead(
        company="ACME", title="Senior Software Engineer",
        apply_url="https://boards.greenhouse.io/acme/jobs/2",
        source="test", source_trust="direct", date_found=date.today(),
        verified=True,
    )

    source = MagicMock()
    source.fetch_new_leads.return_value = [eng_lead]

    generator = MagicMock()
    fake_post = MagicMock()
    fake_post.seo_title = "SE Job"
    fake_post.slug = "se-job"
    fake_post.focus_keyword = "software engineer"
    fake_post.excerpt = "Apply."
    fake_post.body_html = "<p>Job.</p>"
    fake_post.meta_description = "SE job."
    generator.generate.return_value = fake_post

    store = MagicMock()
    store.is_posted.return_value = False

    qc_patch = patch("wfh_pipeline.pipeline.validate_post", return_value=[])
    schema_patch = patch(
        "wfh_pipeline.pipeline.append_jobposting_schema",
        side_effect=lambda html, _: html,
    )

    pipeline = Pipeline(
        source=source,
        generator=generator,
        store=store,
        relevance_filter=None,  # explicitly disabled
    )

    with qc_patch, schema_patch:
        report = pipeline.run(dry_run=True, lane="all")

    assert any(r.action == "dry_run" for r in report.results)
