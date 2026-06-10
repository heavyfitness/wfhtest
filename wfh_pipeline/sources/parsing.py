"""Shared row → Lead parsing for tabular sources (CSV and Google Sheets)."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from ..models import Lead

logger = logging.getLogger(__name__)

TRUE_VALUES = {"true", "1", "yes", "y"}
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_VALUES


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    if raw:
        logger.debug("Unparseable date_found %r; falling back to today", raw)
    return date.today()


def _split_requirements(value: Any) -> list[str]:
    raw = str(value)
    for separator in ("|", ";", "\n"):
        if separator in raw:
            return [part.strip() for part in raw.split(separator) if part.strip()]
    return [raw.strip()] if raw.strip() else []


def lead_from_record(record: Mapping[str, Any], *, source: str) -> Lead | None:
    """Build a Lead from one row; returns None (and logs a warning) for bad rows.

    Header names are normalized ("Apply URL" → apply_url) so sheets with
    human-friendly headers parse the same as the CSV.
    """
    row = {str(key).strip().lower().replace(" ", "_"): value for key, value in record.items()}
    try:
        return Lead(
            id=str(row.get("id", "") or "").strip(),
            company=str(row.get("company", "")),
            title=str(row.get("title", "")),
            pay=str(row.get("pay", "")).strip() or None,
            remote=_to_bool(row.get("remote", True)),
            employment_type=str(row.get("employment_type", "") or "FULL_TIME").strip(),
            requirements=_split_requirements(row.get("requirements", "")),
            description=str(row.get("description", "")),
            apply_url=str(row.get("apply_url", "")).strip(),
            source=str(row.get("source", "")).strip() or source,
            date_found=_to_date(row.get("date_found", "")),
            category=str(row.get("category", "") or "remote-jobs").strip(),
            verified=_to_bool(row.get("verified", False)),
        )
    except ValidationError as exc:
        first_error = exc.errors()[0]
        logger.warning(
            "Skipping malformed lead row (%s — %s): %s: %s",
            row.get("company") or "?",
            row.get("title") or "?",
            ".".join(str(loc) for loc in first_error.get("loc", ())),
            first_error.get("msg", "invalid"),
        )
        return None
