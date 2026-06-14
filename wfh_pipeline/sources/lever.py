"""Lever Job Board API lead source — direct trust lane.

Lever's public postings API (v0) is available for companies that publish
their jobs on Lever and have their board set to public:

    GET https://api.lever.co/v0/postings/{company}?mode=json

where ``{company}`` is the company slug used in their Lever job-board URL
(e.g. ``"whereby"`` for https://jobs.lever.co/whereby).

Finding valid Lever slugs
-------------------------
Look at a company's job page URL — if it is ``jobs.lever.co/{slug}``, that
slug is the company identifier to use here.

.. note::
   The Lever v0 public API has been deprecated for many companies; 404
   responses are common for companies that have migrated to Lever Hire v2 or
   another ATS.  The adapter will log a warning and return an empty list if
   the board is not found.

Configuration
-------------
Add board slugs to ``boards.yaml`` under the ``lever`` key:

.. code-block:: yaml

   lever:
     - whereby
     - acme-corp

Each entry generates a :class:`LeverLeadSource` configured for that company.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import httpx

from ..models import Lead
from ..sources.base import LeadSource
from .greenhouse import ATSLeadSource

logger = logging.getLogger(__name__)

_CATEGORY_MAP = {
    "engineering": "engineering",
    "design": "design",
    "marketing": "marketing",
    "sales": "sales",
    "customer success": "customer-success",
    "support": "customer-success",
    "product": "product",
    "finance": "finance",
    "data": "data",
    "content": "content",
}


def _lever_category(posting: dict) -> str:
    team = str(posting.get("categories", {}).get("team", "")).lower()
    for kw, cat in _CATEGORY_MAP.items():
        if kw in team:
            return cat
    return "remote-jobs"


class LeverLeadSource(ATSLeadSource):
    """Fetches open remote postings from a single Lever job board.

    Parameters
    ----------
    company_slug:
        The company identifier used in their Lever board URL
        (e.g. ``"whereby"`` for ``jobs.lever.co/whereby``).
    require_remote:
        When ``True`` (default), skip postings whose location or tags do not
        contain the word "remote".
    """

    name = "lever"

    _API_BASE = "https://api.lever.co/v0/postings/{company}?mode=json"

    def __init__(
        self,
        company_slug: str,
        *,
        require_remote: bool = True,
    ) -> None:
        self._company_slug = company_slug
        self._require_remote = require_remote

    def fetch_new_leads(self) -> list[Lead]:
        url = self._API_BASE.format(company=self._company_slug)
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url)
        except httpx.HTTPError as exc:
            logger.error("Lever API error for %r: %s", self._company_slug, exc)
            return []

        if resp.status_code == 404:
            logger.warning(
                "Lever board %r not found (404) — company may not use Lever or "
                "their board slug may have changed.",
                self._company_slug,
            )
            return []
        if resp.status_code != 200:
            logger.error(
                "Lever board %r: unexpected status %d", self._company_slug, resp.status_code
            )
            return []

        postings = resp.json()
        if not isinstance(postings, list):
            logger.error("Lever board %r: unexpected response shape", self._company_slug)
            return []

        leads: list[Lead] = []
        for posting in postings:
            lead = self._posting_to_lead(posting)
            if lead is not None:
                leads.append(lead)

        logger.info(
            "Lever[%s]: %d postings fetched, %d converted to leads",
            self._company_slug,
            len(postings),
            len(leads),
        )
        return leads

    def _posting_to_lead(self, posting: dict) -> Lead | None:
        title: str = str(posting.get("text", "")).strip()
        apply_url: str = str(posting.get("applyUrl", "")).strip()
        lever_url: str = str(posting.get("hostedUrl", apply_url)).strip()

        if not title or not lever_url:
            return None

        # Remote filter
        if self._require_remote:
            location = str(posting.get("categories", {}).get("location", "")).lower()
            commitment = str(posting.get("categories", {}).get("commitment", "")).lower()
            tags = [str(t).lower() for t in posting.get("tags", [])]
            is_remote = (
                "remote" in location
                or "remote" in commitment
                or any("remote" in t for t in tags)
            )
            if not is_remote:
                return None

        # Company name from slug (title-cased, dashes→spaces)
        company = self._company_slug.replace("-", " ").title()

        # Date posted
        created_at = posting.get("createdAt")
        try:
            found = datetime.fromtimestamp(created_at / 1000).date() if created_at else date.today()
        except Exception:
            found = date.today()

        description = str(posting.get("descriptionPlain", "") or posting.get("description", ""))
        description = description[:2000]

        category = _lever_category(posting)

        try:
            return Lead(
                company=company,
                title=title,
                apply_url=lever_url,
                source=f"lever:{self._company_slug}",
                source_trust=self.trust,
                description=description,
                category=category,
                date_found=found,
                remote=True,
                verified=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Lever: skipping posting %r (%s): %s", title, self._company_slug, exc)
            return None
