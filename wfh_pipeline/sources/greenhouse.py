"""ATS (Applicant Tracking System) lead sources — direct-trust lane.

``ATSLeadSource`` is an abstract base for any feed whose links go straight to
an employer's own ATS.  All subclasses inherit ``trust = "direct"`` so every
lead they emit enters the direct publishing lane automatically.

``GreenhouseLeadSource`` is a concrete scaffold for the Greenhouse Job Board
API (https://developers.greenhouse.io/job-board.html).  It is intentionally
*not* activated in the pipeline until Ben explicitly adds an employer's board
token to the config — see SCANNER-RUNBOOK.md § ATS Sources.
"""
from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any

import httpx

from ..models import Lead
from ..utils import slugify
from .base import LeadSource

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base for all ATS sources
# ---------------------------------------------------------------------------


class ATSLeadSource(LeadSource):
    """A :class:`LeadSource` whose links are always ATS-direct.

    Subclasses must implement :meth:`fetch_new_leads`.  They inherit
    ``trust = "direct"`` and should pass ``source_trust=self.trust`` when
    constructing :class:`~wfh_pipeline.models.Lead` objects.
    """

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
        Find it at https://boards.greenhouse.io/<board_token>/jobs
    require_remote:
        When ``True`` (default), skip postings not tagged as remote.
    keyword_filters:
        Optional list of case-insensitive strings; at least one must appear in
        the job title or content for the lead to be included.

    Examples
    --------
    >>> source = GreenhouseLeadSource("anthropic")
    >>> leads = source.fetch_new_leads()
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

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def fetch_new_leads(self) -> list[Lead]:
        jobs = self._fetch_jobs()
        leads: list[Lead] = []
        for job in jobs:
            lead = self._job_to_lead(job)
            if lead is not None:
                leads.append(lead)
        logger.info(
            "Greenhouse[%s]: %d jobs fetched, %d converted to leads",
            self._board_token,
            len(jobs),
            len(leads),
        )
        return leads

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_jobs(self) -> list[dict[str, Any]]:
        url = self._API_BASE.format(token=self._board_token)
        params: dict[str, str] = {"content": "true"}
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Greenhouse API error for board %r: %s", self._board_token, exc)
            return []
        data = resp.json()
        return data.get("jobs", [])

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

        # Keyword filter (title or content)
        if self._keyword_filters:
            content_blob = (title + " " + str(job.get("content", ""))).lower()
            if not any(kw in content_blob for kw in self._keyword_filters):
                return None

        apply_url = self._JOB_URL.format(token=self._board_token, job_id=job_id)
        content: str = str(job.get("content", ""))

        try:
            return Lead(
                company=self._board_token.replace("-", " ").title(),
                title=title,
                apply_url=apply_url,
                source=f"greenhouse:{self._board_token}",
                source_trust=self.trust,
                remote=True,
                description=content[:2000],  # cap to avoid huge LLM prompts
                verified=True,  # ATS feed = pre-verified
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping Greenhouse job %s (%s): %s", job_id, title, exc)
            return None
