"""schema.org JobPosting JSON-LD builder.

Every published job post carries its own <script type="application/ld+json">
block so it is eligible for Google for Jobs regardless of theme/plugin config.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from .models import Lead

logger = logging.getLogger(__name__)

DEFAULT_VALID_DAYS = 45

# Substring → schema.org employmentType. Order matters (first match wins).
_EMPLOYMENT_TYPES: tuple[tuple[str, str], ...] = (
    ("full", "FULL_TIME"),
    ("part", "PART_TIME"),
    ("contract", "CONTRACTOR"),
    ("freelance", "CONTRACTOR"),
    ("temp", "TEMPORARY"),
    ("intern", "INTERN"),
    ("volunteer", "VOLUNTEER"),
    ("per diem", "PER_DIEM"),
)

_PAY_NUMBER_RE = re.compile(r"\$?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*([kK])?")


def normalize_employment_type(raw: str) -> str:
    text = raw.strip().lower().replace("-", " ").replace("_", " ")
    for needle, schema_value in _EMPLOYMENT_TYPES:
        if needle in text:
            return schema_value
    return "OTHER"


@dataclass(frozen=True)
class PayInfo:
    min_value: float
    max_value: float
    unit: str  # schema.org unitText: HOUR | DAY | WEEK | MONTH | YEAR


def parse_pay(pay: str | None) -> PayInfo | None:
    """Best-effort parse of a free-text pay string into a salary range.

    Returns None when there is nothing trustworthy to report (no numbers, or a
    piece rate like per-audio-minute that doesn't map onto schema.org units).
    """
    if not pay:
        return None
    text = pay.lower()
    if re.search(r"minute|per audio|per word|per piece|per task", text):
        return None

    values: list[float] = []
    for number, k_suffix in _PAY_NUMBER_RE.findall(pay):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        values.append(value)
    if not values:
        return None

    low, high = min(values[:2]), max(values[:2])
    if re.search(r"/\s*hr\b|hour", text):
        unit = "HOUR"
    elif re.search(r"/\s*yr\b|year|annual|annum", text):
        unit = "YEAR"
    elif "month" in text:
        unit = "MONTH"
    elif "week" in text:
        unit = "WEEK"
    elif "day" in text:
        unit = "DAY"
    else:
        # Heuristic: nobody quotes an annual salary under $200.
        unit = "HOUR" if high < 200 else "YEAR"
    return PayInfo(min_value=low, max_value=high, unit=unit)


def build_job_posting(lead: Lead, *, valid_days: int = DEFAULT_VALID_DAYS) -> dict | None:
    """Build a JobPosting dict from a lead, or None when the lead is too thin."""
    if not lead.description.strip() and not lead.requirements:
        logger.warning(
            "Skipping JobPosting schema for %s (%s — %s): no description or requirements",
            lead.id,
            lead.company,
            lead.title,
        )
        return None

    description_parts: list[str] = []
    if lead.description.strip():
        description_parts.append(f"<p>{html.escape(lead.description.strip())}</p>")
    if lead.requirements:
        items = "".join(f"<li>{html.escape(item)}</li>" for item in lead.requirements)
        description_parts.append(f"<p>Requirements:</p><ul>{items}</ul>")

    posting: dict = {
        "@context": "https://schema.org/",
        "@type": "JobPosting",
        "title": lead.title,
        "description": "".join(description_parts),
        "datePosted": lead.date_found.isoformat(),
        "validThrough": (lead.date_found + timedelta(days=valid_days)).isoformat(),
        "employmentType": normalize_employment_type(lead.employment_type),
        "hiringOrganization": {"@type": "Organization", "name": lead.company},
        "identifier": {"@type": "PropertyValue", "name": lead.company, "value": lead.id},
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "USA"},
        "directApply": True,
    }

    pay = parse_pay(lead.pay)
    if pay is not None:
        posting["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": "USD",
            "value": {
                "@type": "QuantitativeValue",
                "minValue": pay.min_value,
                "maxValue": pay.max_value,
                "unitText": pay.unit,
            },
        }
    return posting


def jsonld_script_tag(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    # "</" inside a string would let embedded HTML close the script tag early.
    payload = payload.replace("</", "<\\/")
    return f'<script type="application/ld+json">{payload}</script>'


def append_jobposting_schema(
    body_html: str, lead: Lead, *, valid_days: int = DEFAULT_VALID_DAYS
) -> str:
    """Return body_html with the JSON-LD block appended (or unchanged if skipped)."""
    posting = build_job_posting(lead, valid_days=valid_days)
    if posting is None:
        return body_html
    return f"{body_html}\n\n{jsonld_script_tag(posting)}"
