"""Optional structured job-API lead source — off by default.

This adapter targets the **JSearch API** available on RapidAPI
(https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch), which provides
structured remote job data from many sources.

Activation
----------
1. Sign up at https://rapidapi.com and subscribe to JSearch (free tier available).
2. Add to ``.env``::

       ENABLE_JOB_API=true
       JOB_API_KEY=your-rapidapi-key

3. Optionally tune the query::

       JOB_API_QUERY=remote software engineer
       JOB_API_MAX_RESULTS=20

The source is **disabled** by default (``ENABLE_JOB_API=false``).  If
``ENABLE_JOB_API`` is not ``true`` the :func:`build_sources` factory never
instantiates this class.

Link classification
-------------------
JSearch results include direct employer apply URLs from many different ATS
platforms.  Each lead's URL is classified by the existing
:func:`~wfh_pipeline.link_classifier.classify_url` logic:

* ATS URL (Greenhouse, Lever, Workday…) → ``link_type="direct"`` → direct lane
* Job board URL → ``link_type="aggregator"`` → aggregator lane

This means a single JSearch run may yield leads in both lanes; the pipeline's
lane filter handles the routing correctly.
"""
from __future__ import annotations

import logging
from datetime import date

import httpx

from ..models import Lead
from .base import LeadSource

logger = logging.getLogger(__name__)

_JSEARCH_URL = "https://jsearch.p.rapidapi.com/search"
_JSEARCH_HOST = "jsearch.p.rapidapi.com"

_TYPE_MAP = {
    "FULLTIME": "FULL_TIME",
    "PARTTIME": "PART_TIME",
    "CONTRACTOR": "CONTRACT",
    "INTERN": "INTERNSHIP",
}


class JobAPILeadSource(LeadSource):
    """Remote job leads from JSearch / RapidAPI (optional, off by default).

    Parameters
    ----------
    api_key:
        RapidAPI key (from ``JOB_API_KEY`` in ``.env``).
    query:
        Search query passed to the API.  Defaults to ``"remote software engineer"``.
    max_results:
        Maximum number of results to fetch (capped by the API at 10 per page;
        we fetch ``ceil(max_results / 10)`` pages).
    """

    name = "job_api"
    trust = "unknown"  # links vary; classify_url() will sort direct vs. aggregator

    def __init__(
        self,
        api_key: str,
        *,
        query: str = "remote software engineer",
        max_results: int = 20,
    ) -> None:
        self._api_key = api_key
        self._query = query
        self._max_results = max_results

    def fetch_new_leads(self) -> list[Lead]:
        import math

        pages = max(1, math.ceil(self._max_results / 10))
        leads: list[Lead] = []
        logger.info("JobAPILeadSource: fetching up to %d results (%d page(s))", self._max_results, pages)

        headers = {
            "x-rapidapi-host": _JSEARCH_HOST,
            "x-rapidapi-key": self._api_key,
        }

        with httpx.Client(timeout=20) as client:
            for page in range(1, pages + 1):
                params = {
                    "query": self._query,
                    "page": str(page),
                    "num_pages": "1",
                    "remote_jobs_only": "true",
                }
                try:
                    resp = client.get(_JSEARCH_URL, headers=headers, params=params)
                    resp.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 403:
                        logger.error(
                            "JobAPILeadSource: 403 Forbidden — check JOB_API_KEY is valid "
                            "and subscribed to JSearch on RapidAPI"
                        )
                    else:
                        logger.error("JobAPILeadSource: HTTP %d on page %d", exc.response.status_code, page)
                    break
                except httpx.HTTPError as exc:
                    logger.error("JobAPILeadSource: request error on page %d: %s", page, exc)
                    break

                data = resp.json()
                jobs = data.get("data", [])
                if not jobs:
                    break

                for job in jobs:
                    lead = self._job_to_lead(job)
                    if lead is not None:
                        leads.append(lead)
                    if len(leads) >= self._max_results:
                        break

                if len(leads) >= self._max_results:
                    break

        logger.info("JobAPILeadSource: %d leads fetched", len(leads))
        return leads

    def _job_to_lead(self, job: dict) -> Lead | None:
        title = str(job.get("job_title", "")).strip()
        company = str(job.get("employer_name", "")).strip()

        # Prefer direct apply URL; fall back to job URL
        apply_url = str(
            job.get("job_apply_link") or job.get("job_google_link") or ""
        ).strip()

        if not title or not company or not apply_url:
            return None

        if not apply_url.startswith(("http://", "https://")):
            return None

        # Employment type
        raw_type = str(job.get("job_employment_type", "FULLTIME")).upper()
        employment_type = _TYPE_MAP.get(raw_type, "FULL_TIME")

        # Date
        posted_at = job.get("job_posted_at_timestamp")
        try:
            if posted_at:
                from datetime import datetime
                found = datetime.fromtimestamp(int(posted_at)).date()
            else:
                found = date.today()
        except Exception:
            found = date.today()

        # Category — map job_required_skills or title keywords
        category = _guess_category(title, job.get("job_required_skills") or [])

        description = str(job.get("job_description", ""))[:2000]

        try:
            return Lead(
                company=company,
                title=title,
                apply_url=apply_url,
                source=f"api:{self.name}",
                source_trust=self.trust,
                description=description,
                employment_type=employment_type,
                category=category,
                date_found=found,
                remote=True,
                verified=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("JobAPILeadSource: skipping %r / %s: %s", title, company, exc)
            return None


def _guess_category(title: str, skills: list) -> str:
    blob = (title + " " + " ".join(str(s) for s in skills)).lower()
    if any(kw in blob for kw in ("engineer", "developer", "devops", "backend", "frontend", "fullstack", "sre", "platform")):
        return "engineering"
    if any(kw in blob for kw in ("design", "ux", "ui ", "product designer")):
        return "design"
    if any(kw in blob for kw in ("marketing", "seo", "content", "social media", "copywriter")):
        return "marketing"
    if any(kw in blob for kw in ("sales", "account executive", "business development")):
        return "sales"
    if any(kw in blob for kw in ("customer success", "support", "customer service")):
        return "customer-success"
    if any(kw in blob for kw in ("product manager", "product owner")):
        return "product"
    if any(kw in blob for kw in ("data scientist", "data analyst", "ml engineer", "machine learning", "analyst")):
        return "data"
    if any(kw in blob for kw in ("finance", "accountant", "cfo", "bookkeep")):
        return "finance"
    return "remote-jobs"
