"""ATS (Applicant Tracking System) lead sources — direct-trust lane."""
from __future__ import annotations

import html as html_mod
import logging
import re
from abc import abstractmethod
from typing import Any

import httpx

from ..models import Lead
from .base import LeadSource

logger = logging.getLogger(__name__)

# Max chars of clean description text passed to the LLM prompt.
_DESC_MAX = 3000

# ---------------------------------------------------------------------------
# HTML parsing helpers — no external deps required
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s{2,}")


def _strip_html(raw: str) -> str:
    """Decode HTML entities and strip all tags, collapsing extra whitespace."""
    decoded = html_mod.unescape(raw)
    plain = _TAG_RE.sub(" ", decoded)
    return _WHITESPACE_RE.sub(" ", plain).strip()


def _extract_li_items(raw: str) -> list[str]:
    """Pull text from every <li>…</li> block in an HTML string.

    Returns a list of clean strings, deduped and capped at 30 items.
    """
    items: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"<li[^>]*>(.*?)</li>", raw, re.DOTALL | re.IGNORECASE):
        text = _strip_html(match.group(1)).strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items[:30]


def _parse_greenhouse_content(raw_html: str) -> tuple[str, list[str]]:
    """Parse a Greenhouse posting's ``content`` HTML field.

    Returns ``(clean_description, requirements_list)`` where
    ``clean_description`` is the full text with tags/entities stripped and
    ``requirements_list`` contains all ``<li>`` items found in the HTML
    (typically qualifications, responsibilities, and nice-to-haves).
    """
    requirements = _extract_li_items(raw_html)
    clean_text = _strip_html(raw_html)
    return clean_text, requirements


# ---------------------------------------------------------------------------
# Abstract base for all ATS sources
# ---------------------------------------------------------------------------


class ATSLeadSource(LeadSource):
    """A LeadSource whose links are always ATS-direct."""

    trust = "direct"

    @abstractmethod
    def fetch_new_leads(self) -> list[Lead]: ...


# ---------------------------------------------------------------------------
# Greenhouse Job Board API
# ---------------------------------------------------------------------------


class GreenhouseLeadSource(ATSLeadSource):
    """Fetches open remote jobs from a single Greenhouse job board.

    Parameters
    ----------
    board_token:
        The employer's Greenhouse board token (e.g. ``"anthropic"``).
    require_remote:
        Skip postings not tagged as remote (default: True).
    keyword_filters:
        Optional case-insensitive strings; at least one must appear in
        the job title or content for the lead to be included.
    """

    name = "greenhouse"

    _API_BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    _JOB_URL = "https://boards.greenhouse.io/{token}/jobs/{job_id}"

    def __init__(
        self,
        board_token: str,
        *,
        require_remote: bool = True,
        keyword_filters: list[str] | None = None,
    ) -> None:
        self._board_token = board_token
        self._require_remote = require_remote
        self._keyword_filters = [kw.lower() for kw in (keyword_filters or [])]

    def fetch_new_leads(self) -> list[Lead]:
        jobs = self._fetch_jobs()
        leads: list[Lead] = []
        for job in jobs:
            lead = self._job_to_lead(job)
            if lead is not None:
                leads.append(lead)
        logger.info(
            "Greenhouse[%s]: %d jobs fetched, %d converted to leads",
            self._board_token, len(jobs), len(leads),
        )
        return leads

    def _fetch_jobs(self) -> list[dict[str, Any]]:
        url = self._API_BASE.format(token=self._board_token)
        try:
            with httpx.Client(timeout=15) as client:
                # content=true fetches the full job description HTML
                resp = client.get(url, params={"content": "true"})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Greenhouse API error for board %r: %s", self._board_token, exc)
            return []
        return resp.json().get("jobs", [])

    def _job_to_lead(self, job: dict[str, Any]) -> Lead | None:
        job_id = job.get("id")
        title: str = str(job.get("title", "")).strip()
        if not title or not job_id:
            return None

        # Remote filter
        if self._require_remote:
            location: str = str(job.get("location", {}).get("name", "")).lower()
            if "remote" not in location:
                return None

        raw_content: str = str(job.get("content", ""))

        # Keyword filter (title + cleaned content)
        if self._keyword_filters:
            content_blob = (title + " " + _strip_html(raw_content)).lower()
            if not any(kw in content_blob for kw in self._keyword_filters):
                return None

        # Parse the HTML content: decode entities, strip tags, extract <li> items
        clean_description, requirements = _parse_greenhouse_content(raw_content)

        apply_url = self._JOB_URL.format(token=self._board_token, job_id=job_id)

        try:
            return Lead(
                company=self._board_token.replace("-", " ").title(),
                title=title,
                apply_url=apply_url,
                source=f"greenhouse:{self._board_token}",
                source_trust=self.trust,
                remote=True,
                # Cleaned plain text — the LLM sees real prose, not HTML tags
                description=clean_description[:_DESC_MAX],
                # Extracted bullet items from the posting (qualifications etc.)
                requirements=requirements,
                verified=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping Greenhouse job %s (%s): %s", job_id, title, exc)
            return None
