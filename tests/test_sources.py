"""Unit tests for the CSV lead source and shared row parsing."""
from __future__ import annotations

from conftest import SAMPLE_CSV

from wfh_pipeline.sources.csv_source import CSVLeadSource
from wfh_pipeline.sources.parsing import lead_from_record


def test_csv_source_returns_only_verified_leads() -> None:
    leads = CSVLeadSource(SAMPLE_CSV).fetch_new_leads()
    assert len(leads) == 4
    assert all(lead.verified for lead in leads)
    assert "QuickCash Gigs" not in {lead.company for lead in leads}


def test_csv_fields_parsed() -> None:
    leads = {lead.company: lead for lead in CSVLeadSource(SAMPLE_CSV).fetch_new_leads()}
    brightdesk = leads["BrightDesk Solutions"]
    assert len(brightdesk.requirements) == 4
    assert brightdesk.pay == "$17-$19/hr"
    assert brightdesk.category == "customer-service"
    assert brightdesk.date_found.isoformat() == "2026-06-08"
    assert brightdesk.id  # derived hash present
    assert leads["ScriptWave Media"].employment_type == "CONTRACTOR"


def test_bad_row_returns_none_instead_of_raising() -> None:
    record = {
        "company": "Acme",
        "title": "Tester",
        "apply_url": "not-a-url",
        "verified": "TRUE",
    }
    assert lead_from_record(record, source="test") is None


def test_header_names_are_normalized() -> None:
    record = {
        "Company": "Acme",
        "Title": "QA Tester",
        "Apply URL": "https://example.com/apply",
        "Verified": "yes",
        "Employment Type": "part-time",
    }
    lead = lead_from_record(record, source="test")
    assert lead is not None
    assert lead.company == "Acme"
    assert lead.verified is True
    assert lead.employment_type == "part-time"
    assert lead.source == "test"
