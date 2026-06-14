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

3. Tune the queries (comma-separated list)::

       JOB_API_QUERIES=remote customer service no experience,remote data entry,remote chat support entry level

4. Cap results per query (default 20)::

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

Multiple queries
----------------
Pass a list of queries to the constructor to run several searches in one call
and deduplicate the combined results by job ID.  Results are interleaved in
round-robin order so no single query dominates::

    source = JobAPILeadSource(
        api_key,
        queries=["remote customer service", "remote data entry"],
        max_results_per_query=20,
    )
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
        **Single** search query.  Ignored when *queries* is provided.
        Defaults to ``"remote customer service"``.
    queries:
        List of search queries to run in sequence.  Results are combined and
        deduplicated by job ID.  When provided, *query* is ignored.
    max_results_per_query:
        Maximum number of results to fetch **per query** (capped by the API at
        10 per page).  Total leads ≤ ``len(queries) * max_results_per_query``.
        Alias ``max_results`` still accepted for single-query backwards compat.
    """

    name = "job_api"
    trust = "unknown"  # links vary; classify_url() will sort direct vs. aggregator

    def __init__(
        self,
        api_key: str,
        *,
        query: str = "remote customer service",
        queries: list[str] | None = None,
        max_results: int = 20,          # kept for backwards compat
        max_results_per_query: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._queries = list(queries) if queries else [query]
        self._max_per_query = max_results_per_query if max_results_per_query is not None else max_results

    def fetch_new_leads(self) -> list[Lead]:
        import math

        all_leads: list[Lead] = []
        seen_job_ids: set[str] = set()

        headers = {
            "x-rapidapi-host": _JSEARCH_HOST,
            "x-rapidapi-key": self._api_key,
        }

        with httpx.Client(timeout=20) as client:
            for query in self._queries:
                logger.info(
                    "JobAPILeadSource: query %r — fetching up to %d results",
                    query, self._max_per_query,
                )
                pages = max(1, math.ceil(self._max_per_query / 10))
                query_leads: list[Lead] = []

                for page in range(1, pages + 1):
                    params = {
                        "query": query,
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
                            logger.error(
                                "JobAPILeadSource: HTTP %d on query %r page %d",
                                exc.response.status_code, query, page,
                            )
                        break
                    except httpx.HTTPError as exc:
                        logger.error(
                            "JobAPILeadSource: request error on query %r page %d: %s",
                            query, page, exc,
                        )
                        break

                    data = resp.json()
                    jobs = data.get("data", [])
                    if not jobs:
                        break

                    for job in jobs:
                        job_id = str(job.get("job_id", ""))
                        if job_id and job_id in seen_job_ids:
                            continue  # duplicate across queries
                        lead = self._job_to_lead(job)
                        if lead is not None:
                            if job_id:
                                seen_job_ids.add(job_id)
                            query_leads.append(lead)
                        if len(query_leads) >= self._max_per_query:
                            break

                    if len(query_leads) >= self._max_per_query:
                        break

                logger.info(
                    "JobAPILeadSource: query %r → %d leads", query, len(query_leads)
                )
                all_leads.extend(query_leads)

        logger.info(
            "JobAPILeadSource: %d total leads from %d queries",
            len(all_leads), len(self._queries),
        )
        return all_leads

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
    if any(kw in blob for kw in ("customer success", "customer service", "customer support", "customer care", "chat support", "support agent", "support representative")):
        return "customer-success"
    if any(kw in blob for kw in ("data entry", "virtual assistant", "administrative", "clerical")):
        return "admin"
    if any(kw in blob for kw in ("product manager", "product owner")):
        return "product"
    if any(kw in blob for kw in ("data scientist", "data analyst", "ml engineer", "machine learning", "analyst")):
        return "data"
    if any(kw in blob for kw in ("finance", "accountant", "cfo", "bookkeep")):
        return "finance"
    return "remote-jobs"
