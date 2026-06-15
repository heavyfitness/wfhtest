"""Unit tests for the schema.org JobPosting builder."""
from __future__ import annotations

from datetime import date, timedelta

from wfh_pipeline.schema import (
    DEFAULT_VALID_DAYS,
    append_jobposting_schema,
    build_job_posting,
    jsonld_script_tag,
    normalize_employment_type,
    parse_pay,
)


def test_build_job_posting_core_fields(make_lead) -> None:
    lead = make_lead(date_found=date(2026, 6, 8))
    posting = build_job_posting(lead)
    assert posting is not None
    assert posting["@type"] == "JobPosting"
    assert posting["title"] == lead.title
    assert posting["hiringOrganization"] == {"@type": "Organization", "name": lead.company}
    assert posting["jobLocationType"] == "TELECOMMUTE"
    assert posting["applicantLocationRequirements"] == {"@type": "Country", "name": "USA"}
    assert posting["directApply"] is True
    assert posting["datePosted"] == "2026-06-08"
    assert posting["validThrough"] == (date(2026, 6, 8) + timedelta(days=DEFAULT_VALID_DAYS)).isoformat()
    assert posting["employmentType"] == "FULL_TIME"
    assert posting["identifier"]["value"] == lead.id


def test_base_salary_from_hourly_range(make_lead) -> None:
    posting = build_job_posting(make_lead(pay="$17 - $19 / hr"))
    assert posting is not None
    salary = posting["baseSalary"]["value"]
    assert salary["minValue"] == 17.0
    assert salary["maxValue"] == 19.0
    assert salary["unitText"] == "HOUR"


def test_pay_parsing_variants() -> None:
    yearly = parse_pay("$45k-$55k per year")
    assert yearly is not None
    assert (yearly.min_value, yearly.max_value, yearly.unit) == (45000.0, 55000.0, "YEAR")

    bare_yearly = parse_pay("$52,000")
    assert bare_yearly is not None
    assert bare_yearly.unit == "YEAR"  # >= $200 heuristic

    assert parse_pay("Competitive") is None
    assert parse_pay(None) is None
    assert parse_pay("$0.60 per audio minute") is None  # piece rates omitted


def test_base_salary_omitted_when_unparseable(make_lead) -> None:
    posting = build_job_posting(make_lead(pay="Competitive"))
    assert posting is not None
    assert "baseSalary" not in posting


def test_employment_type_normalization() -> None:
    assert normalize_employment_type("full-time") == "FULL_TIME"
    assert normalize_employment_type("Part Time") == "PART_TIME"
    assert normalize_employment_type("CONTRACTOR") == "CONTRACTOR"
    assert normalize_employment_type("freelance") == "CONTRACTOR"
    assert normalize_employment_type("Temporary") == "TEMPORARY"
    assert normalize_employment_type("???") == "OTHER"


def test_schema_skipped_when_lead_too_thin(make_lead) -> None:
    lead = make_lead(description="", requirements=[])
    assert build_job_posting(lead) is None
    body = "<p>hello</p>"
    assert append_jobposting_schema(body, lead) == body  # unchanged, just logged


def test_script_tag_cannot_be_broken_out_of() -> None:
    tag = jsonld_script_tag({"description": "</script><b>boom</b>"})
    # Only the legitimate closing tag survives; embedded one is escaped.
    assert tag.count("</script>") == 1
    assert "<\\/script>" in tag


def test_append_adds_script_block(make_lead) -> None:
    body = "<p>post body</p>"
    result = append_jobposting_schema(body, make_lead())
    assert result.startswith(body)
    assert '<script type="application/ld+json">' in result
