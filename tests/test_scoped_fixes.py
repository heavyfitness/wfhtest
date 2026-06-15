"""Tests for the four scoped fixes:
   Bug 1 — Greenhouse HTML parsing + JSearch highlights + prompt requirements
   Bug 2 — Relevance filter: years-of-experience rejection + updated keywords
   Change 3 — Non-phone detection and automatic WP tag
   Change 4 — Phone/call-center queries in defaults
"""
from __future__ import annotations

import re
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from wfh_pipeline.models import Lead

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lead(
    title: str,
    description: str = "",
    requirements: list[str] | None = None,
    *,
    apply_url: str = "https://boards.greenhouse.io/acme/jobs/1",
) -> Lead:
    return Lead(
        company="Acme",
        title=title,
        apply_url=apply_url,
        source="test",
        source_trust="direct",
        description=description,
        requirements=requirements or [],
        date_found=date.today(),
        verified=True,
    )


# ===========================================================================
# Bug 1 — Greenhouse HTML parsing
# ===========================================================================


class TestGreenhouseHTMLParsing:
    def test_strip_tags_produces_plain_text(self):
        from wfh_pipeline.sources.greenhouse import _strip_html

        raw = "<p>We are <strong>hiring</strong> a rep.</p><ul><li>Python</li></ul>"
        result = _strip_html(raw)
        assert "<" not in result
        assert "We are hiring a rep." in result
        assert "Python" in result

    def test_html_entities_decoded(self):
        from wfh_pipeline.sources.greenhouse import _strip_html

        raw = "5+ years&nbsp;&amp; strong communication skills&#8212;required"
        result = _strip_html(raw)
        assert "&amp;" not in result
        assert "&nbsp;" not in result
        assert "5+ years" in result

    def test_extract_li_items(self):
        from wfh_pipeline.sources.greenhouse import _extract_li_items

        html = """
        <ul>
          <li>2+ years customer service experience</li>
          <li>Strong written communication</li>
          <li>Comfortable with CRM tools</li>
        </ul>
        """
        items = _extract_li_items(html)
        assert len(items) == 3
        assert "2+ years customer service experience" in items
        assert "Strong written communication" in items

    def test_extract_li_items_deduplicates(self):
        from wfh_pipeline.sources.greenhouse import _extract_li_items

        html = "<li>Skill A</li><li>Skill A</li><li>Skill B</li>"
        items = _extract_li_items(html)
        assert items.count("Skill A") == 1

    def test_parse_greenhouse_content_returns_tuple(self):
        from wfh_pipeline.sources.greenhouse import _parse_greenhouse_content

        html = (
            "<h2>About the role</h2><p>Join our team of &amp; support agents.</p>"
            "<h2>Requirements</h2><ul><li>CRM experience</li><li>Chat support</li></ul>"
        )
        desc, reqs = _parse_greenhouse_content(html)
        assert "&amp;" not in desc
        assert "<" not in desc
        assert "About the role" in desc
        assert "CRM experience" in reqs
        assert "Chat support" in reqs

    def test_greenhouse_lead_has_clean_description(self):
        """GreenhouseLeadSource._job_to_lead produces clean description (no HTML)."""
        from wfh_pipeline.sources.greenhouse import GreenhouseLeadSource

        src = GreenhouseLeadSource("acme")
        job = {
            "id": 99,
            "title": "Remote Customer Service Rep",
            "location": {"name": "Remote"},
            "content": (
                "<p>You will handle customer inquiries via chat &amp; email.</p>"
                "<ul><li>High school diploma or GED</li>"
                "<li>1+ years customer service</li></ul>"
            ),
        }
        lead = src._job_to_lead(job)
        assert lead is not None
        assert "<" not in lead.description, "HTML tags must be stripped"
        assert "&amp;" not in lead.description, "HTML entities must be decoded"
        assert "chat" in lead.description.lower()
        assert len(lead.requirements) == 2
        assert any("high school" in r.lower() for r in lead.requirements)
        assert any("customer service" in r.lower() for r in lead.requirements)

    def test_greenhouse_lead_no_content_ok(self):
        from wfh_pipeline.sources.greenhouse import GreenhouseLeadSource

        src = GreenhouseLeadSource("acme")
        job = {
            "id": 1,
            "title": "Remote Support Agent",
            "location": {"name": "Remote"},
            "content": "",
        }
        lead = src._job_to_lead(job)
        assert lead is not None
        assert lead.description == ""
        assert lead.requirements == []


class TestJobAPIHighlights:
    def test_highlights_qualifications_become_requirements(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        src = JobAPILeadSource("key")
        job = {
            "job_id": "j1",
            "job_title": "Customer Service Rep",
            "employer_name": "Acme",
            "job_apply_link": "https://jobs.acme.com/apply/1",
            "job_employment_type": "FULLTIME",
            "job_posted_at_timestamp": None,
            "job_description": "Handle customer inquiries.",
            "job_required_skills": [],
            "job_highlights": {
                "Qualifications": ["High school diploma", "Strong communication"],
                "Responsibilities": ["Answer phones", "Log tickets"],
            },
        }
        lead = src._job_to_lead(job)
        assert lead is not None
        assert "High school diploma" in lead.requirements
        assert "Strong communication" in lead.requirements
        # Responsibilities go into description, not requirements
        assert "Answer phones" not in lead.requirements
        assert "Responsibilities" in lead.description

    def test_no_highlights_still_works(self):
        from wfh_pipeline.sources.job_api import JobAPILeadSource

        src = JobAPILeadSource("key")
        job = {
            "job_id": "j2",
            "job_title": "Data Entry Clerk",
            "employer_name": "Corp",
            "job_apply_link": "https://corp.com/jobs/2",
            "job_employment_type": "FULLTIME",
            "job_posted_at_timestamp": None,
            "job_description": "Enter data into spreadsheets.",
            "job_required_skills": [],
            "job_highlights": None,
        }
        lead = src._job_to_lead(job)
        assert lead is not None
        assert lead.requirements == []
        assert "Enter data" in lead.description


class TestPromptRequirements:
    def test_requirements_list_used_when_populated(self):
        from wfh_pipeline.generation.prompts import _format_requirements

        lead = _lead("CS Rep", requirements=["Fluent English", "CRM experience"])
        result = _format_requirements(lead)
        assert "Fluent English" in result
        assert "CRM experience" in result
        assert "none listed" not in result.lower()
        assert "extract" not in result.lower()

    def test_empty_requirements_with_description_instructs_llm_to_extract(self):
        from wfh_pipeline.generation.prompts import _format_requirements

        lead = _lead("CS Rep", description="Must have 1 year experience. Strong Excel skills required.")
        result = _format_requirements(lead)
        # Must NOT claim none listed when description has content
        assert "none listed" not in result.lower()
        # Must instruct LLM to extract from description
        assert "extract" in result.lower() or "description" in result.lower()

    def test_empty_requirements_empty_description_says_none(self):
        from wfh_pipeline.generation.prompts import _format_requirements

        lead = _lead("CS Rep", description="", requirements=[])
        result = _format_requirements(lead)
        assert "none listed" in result.lower()

    def test_build_user_prompt_includes_description(self):
        from wfh_pipeline.generation.prompts import build_user_prompt

        lead = _lead(
            "Customer Support Agent",
            description="You will handle chat inquiries. Requirements: 6 months experience.",
        )
        prompt = build_user_prompt(lead)
        assert "chat inquiries" in prompt
        assert "Requirements" in prompt


# ===========================================================================
# Bug 2 — Relevance filter: years-of-experience rejection
# ===========================================================================


class TestExperienceYearsRejection:
    def test_3_plus_years_rejected(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert not rf.is_relevant("Customer Service Rep", "Must have 3+ years of experience.")

    def test_5_plus_years_rejected(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert not rf.is_relevant("Support Agent", "Requires 5+ years of customer service experience.")

    def test_minimum_of_4_years_rejected(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert not rf.is_relevant("Rep", "Minimum of 4 years relevant work experience required.")

    def test_at_least_3_years_rejected(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert not rf.is_relevant("Agent", "At least 3 years in a call center environment.")

    def test_7_years_required_rejected(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert not rf.is_relevant("Coordinator", "7 years of experience managing accounts.")

    def test_2_years_accepted(self):
        """2 years is below threshold — should not be rejected by YOE check alone."""
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        # 2 years < threshold of 3, so YOE check passes
        assert rf.is_relevant("CS Rep", "1-2 years of experience preferred.")

    def test_no_experience_required_accepted(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=True)
        assert rf.is_relevant("Data Entry Clerk", "No experience required. We will train you.")

    def test_yoe_check_disabled(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter(include_keywords=(), exclude_title_keywords=(), reject_experience_years=False)
        # With check disabled, high-experience role passes (other filters permissive)
        assert rf.is_relevant("CS Rep", "5+ years of customer service experience required.")

    def test_payments_strategist_rejected_by_yoe(self):
        """The 'Payments Performance Strategist' example from the bug report."""
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter.from_env()
        # Title would pass title-exclude check, but description has 5+ years
        desc = (
            "The Payments Performance Strategist will optimize payment flows. "
            "Requirements: 5+ years experience in payments or fintech."
        )
        assert not rf.is_relevant("Payments Performance Strategist", desc)

    def test_no_experience_customer_service_kept(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter.from_env()
        assert rf.is_relevant(
            "Remote Customer Service Representative",
            "No experience necessary. Full training provided. Entry-level role.",
        )

    def test_junior_data_entry_kept(self):
        from wfh_pipeline.relevance import RelevanceFilter

        rf = RelevanceFilter.from_env()
        assert rf.is_relevant(
            "Junior Data Entry Specialist",
            "Looking for a detail-oriented person to enter data. No prior experience needed.",
        )


class TestUpdatedKeywords:
    def test_call_center_in_include_defaults(self):
        from wfh_pipeline.relevance import _DEFAULT_INCLUDE

        assert "call center" in _DEFAULT_INCLUDE

    def test_experienced_in_exclude_defaults(self):
        from wfh_pipeline.relevance import _DEFAULT_EXCLUDE_TITLE

        # "experienced " or " experienced" should be in excludes
        blob = " ".join(_DEFAULT_EXCLUDE_TITLE)
        assert "experienced" in blob

    def test_min_years_regex(self):
        from wfh_pipeline.relevance import _min_years_required

        assert _min_years_required("3+ years of experience") == 3
        assert _min_years_required("minimum of 5 years") == 5
        assert _min_years_required("at least 4 years relevant") == 4
        assert _min_years_required("7 years") == 7
        assert _min_years_required("1-2 years preferred") == 2
        assert _min_years_required("no experience required") is None


# ===========================================================================
# Change 3 — Non-phone detection
# ===========================================================================


class TestNonPhoneDetection:
    def test_chat_support_is_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert is_non_phone("Chat Support Agent", "Respond to customers via chat.")

    def test_email_support_is_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert is_non_phone("Remote Email Support Rep", "Handle email inquiries.")

    def test_data_entry_is_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert is_non_phone("Data Entry Clerk", "Enter records into the database.")

    def test_non_phone_in_title(self):
        from wfh_pipeline.utils import is_non_phone

        assert is_non_phone("Non-Phone Remote Customer Service", "")

    def test_back_office_is_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert is_non_phone("Back Office Associate", "Process orders and tickets.")

    def test_call_center_is_not_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert not is_non_phone("Call Center Rep", "Handle inbound calls from customers.")

    def test_phone_support_is_not_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert not is_non_phone("Customer Service Rep", "You will make and receive phone calls.")

    def test_generic_cs_with_no_signals_is_not_non_phone(self):
        from wfh_pipeline.utils import is_non_phone

        assert not is_non_phone("Customer Service Representative", "Assist customers with their needs.")

    def test_phone_signal_overrides_non_phone_title(self):
        """If title says 'chat' but description mentions phone, phone wins."""
        from wfh_pipeline.utils import is_non_phone

        assert not is_non_phone("Chat Support", "You will also handle inbound calls when needed.")

    def test_pipeline_adds_non_phone_tag_on_publish(self):
        """Pipeline._publish calls get_or_create_tag with non_phone_tag for non-phone leads."""
        from unittest.mock import MagicMock, patch

        from wfh_pipeline.pipeline import Pipeline

        lead = _lead("Chat Support Agent", description="Handle customer queries via live chat.")
        post = MagicMock()
        post.seo_title = "Chat Support Job"
        post.slug = "chat-support-job"
        post.focus_keyword = "chat support"
        post.excerpt = "A chat job."
        post.body_html = "<p>Job.</p>"
        post.meta_description = "Chat support job."

        wp = MagicMock()
        wp.get_or_create_category.return_value = 1
        wp.get_or_create_tag.return_value = 2
        wp.create_post.return_value = {"id": 99, "link": "https://example.com/p/99"}

        store = MagicMock()
        store.is_posted.return_value = False

        pipeline = Pipeline(
            source=MagicMock(),
            generator=MagicMock(),
            store=store,
            wordpress=wp,
            non_phone_tag="Non-Phone",
        )
        pipeline._publish(lead, post, "<p>Job.</p>", status="draft", scheduled_for=None)

        # Verify get_or_create_tag was called with "Non-Phone"
        tag_calls = [call.args[0] for call in wp.get_or_create_tag.call_args_list]
        assert "Non-Phone" in tag_calls, f"Expected 'Non-Phone' in tag calls, got: {tag_calls}"

    def test_pipeline_no_non_phone_tag_for_phone_role(self):
        """Pipeline._publish does NOT add non_phone_tag for phone-based roles."""
        from unittest.mock import MagicMock

        from wfh_pipeline.pipeline import Pipeline

        lead = _lead("Call Center Rep", description="Handle inbound phone calls from customers.")
        post = MagicMock()
        post.seo_title = "Call Center Rep Job"
        post.slug = "call-center-rep"
        post.focus_keyword = "call center"
        post.excerpt = "A phone job."
        post.body_html = "<p>Job.</p>"
        post.meta_description = "Call center job."

        wp = MagicMock()
        wp.get_or_create_category.return_value = 1
        wp.get_or_create_tag.return_value = 2
        wp.create_post.return_value = {"id": 100, "link": "https://example.com/p/100"}

        store = MagicMock()
        pipeline = Pipeline(
            source=MagicMock(), generator=MagicMock(), store=store,
            wordpress=wp, non_phone_tag="Non-Phone",
        )
        pipeline._publish(lead, post, "<p>Job.</p>", status="draft", scheduled_for=None)

        tag_calls = [call.args[0] for call in wp.get_or_create_tag.call_args_list]
        assert "Non-Phone" not in tag_calls, f"Phone role should not get Non-Phone tag, got: {tag_calls}"

    def test_empty_non_phone_tag_disables_feature(self):
        """Setting non_phone_tag='' skips auto-tagging entirely."""
        from unittest.mock import MagicMock

        from wfh_pipeline.pipeline import Pipeline

        lead = _lead("Chat Support Agent", description="Handle customer queries via live chat.")
        post = MagicMock()
        post.seo_title = "Chat Job"
        post.slug = "chat-job"
        post.focus_keyword = "chat"
        post.excerpt = "."
        post.body_html = "<p>.</p>"
        post.meta_description = "."

        wp = MagicMock()
        wp.get_or_create_category.return_value = 1
        wp.get_or_create_tag.return_value = 2
        wp.create_post.return_value = {"id": 101, "link": "https://example.com/p/101"}

        store = MagicMock()
        pipeline = Pipeline(
            source=MagicMock(), generator=MagicMock(), store=store,
            wordpress=wp, non_phone_tag="",  # disabled
        )
        pipeline._publish(lead, post, "<p>.</p>", status="draft", scheduled_for=None)

        # With empty tag name, no extra tag should be added beyond company + category
        tag_calls = [call.args[0] for call in wp.get_or_create_tag.call_args_list]
        assert len(tag_calls) == 2, f"Expected 2 tags (company + category), got: {tag_calls}"


# ===========================================================================
# Change 4 — Phone queries in default JOB_API_QUERIES
# ===========================================================================


class TestDefaultJobAPIQueries:
    def test_call_center_query_in_defaults(self):
        from wfh_pipeline.config import _DEFAULT_JOB_API_QUERIES

        joined = " ".join(_DEFAULT_JOB_API_QUERIES).lower()
        assert "call center" in joined

    def test_inbound_query_in_defaults(self):
        from wfh_pipeline.config import _DEFAULT_JOB_API_QUERIES

        joined = " ".join(_DEFAULT_JOB_API_QUERIES).lower()
        assert "inbound" in joined

    def test_non_phone_query_also_present(self):
        from wfh_pipeline.config import _DEFAULT_JOB_API_QUERIES

        joined = " ".join(_DEFAULT_JOB_API_QUERIES).lower()
        # Non-phone queries should still be present
        assert "chat support" in joined or "data entry" in joined

    def test_at_least_6_default_queries(self):
        from wfh_pipeline.config import _DEFAULT_JOB_API_QUERIES

        assert len(_DEFAULT_JOB_API_QUERIES) >= 6
