"""Optional structured job-API lead source — off by default.

Targets the JSearch API on RapidAPI (https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch).

Activation
----------
Set in .env::

    ENABLE_JOB_API=true
    JOB_API_KEY=your-rapidapi-key
    JOB_API_QUERIES=remote customer service no experience,remote data entry,remote call center
    JOB_API_MAX_RESULTS=20

Multiple queries run independently; results are combined and deduplicated by job_id.

Link classification
-------------------
JSearch apply URLs may point to direct ATS pages (Greenhouse, Lever, Workday…) or
to job boards — classify_url() sorts them automatically per lead.
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

# JSearch job_highlights keys that contain requirements / qualifications text.
_HIGHLIGHTS_REQUIREMENT_KEYS = (
    "Qualifications",
    "Required Qualifications",
    "Requirements",
    "Must Have",
    "Skills",
)
_HIGHLIGHTS_DESCRIPTION_KEYS = (
    "Responsibilities",
    "Job Description",
    "About the Role",
    "What You'll Do",
    "Benefits",
)


class JobAPILeadSource(LeadSource):
    """Remote job leads from JSearch / RapidAPI (optional, off by default).

    Parameters
    ----------
    api_key:
        RapidAPI key.
    query:
        Single search query (ignored when *queries* provided).
    queries:
        List of search queries run in sequence; results deduped by job_id.
    max_results / max_results_per_query:
        Max results per query (10 per API page).
    """

    name = "job_api"
    trust = "unknown"

    def __init__(
        self,
        api_key: str,
        *,
        query: str = "remote customer service",
        queries: list[str] | None = None,
        max_results: int = 20,
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
                    "JobAPILeadSource: query %r — up to %d results",
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
                                "JobAPILeadSource: 403 Forbidden — check JOB_API_KEY "
                                "and JSearch subscription on RapidAPI"
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

                    jobs = resp.json().get("data", [])
                    if not jobs:
                        break

                    for job in jobs:
                        job_id = str(job.get("job_id", ""))
                        if job_id and job_id in seen_job_ids:
                            continue
                        lead = self._job_to_lead(job)
                        if lead is not None:
                            if job_id:
                                seen_job_ids.add(job_id)
                            query_leads.append(lead)
                        if len(query_leads) >= self._max_per_query:
                            break

                    if len(query_leads) >= self._max_per_query:
                        break

                logger.info("JobAPILeadSource: query %r → %d leads", query, len(query_leads))
                all_leads.extend(query_leads)

        logger.info(
            "JobAPILeadSource: %d total leads from %d queries",
            len(all_leads), len(self._queries),
        )
        return all_leads

    def _job_to_lead(self, job: dict) -> Lead | None:
        title = str(job.get("job_title", "")).strip()
        company = str(job.get("employer_name", "")).strip()
        apply_url = str(
            job.get("job_apply_link") or job.get("job_google_link") or ""
        ).strip()

        if not title or not company or not apply_url:
            return None
        if not apply_url.startswith(("http://", "https://")):
            return None

        employment_type = _TYPE_MAP.get(
            str(job.get("job_employment_type", "FULLTIME")).upper(), "FULL_TIME"
        )

        posted_at = job.get("job_posted_at_timestamp")
        try:
            if posted_at:
                from datetime import datetime
                found = datetime.fromtimestamp(int(posted_at)).date()
            else:
                found = date.today()
        except Exception:
            found = date.today()

        category = _guess_category(title, job.get("job_required_skills") or [])

        # ------------------------------------------------------------------
        # Description: combine job_description + highlights description keys
        # ------------------------------------------------------------------
        raw_desc = str(job.get("job_description", ""))
        highlights: dict = job.get("job_highlights", {}) or {}

        extra_desc_parts: list[str] = []
        for key in _HIGHLIGHTS_DESCRIPTION_KEYS:
            items = highlights.get(key)
            if items and isinstance(items, list):
                block = f"{key}:\n" + "\n".join(f"- {i}" for i in items)
                extra_desc_parts.append(block)

        full_desc = raw_desc
        if extra_desc_parts:
            full_desc = (raw_desc + "\n\n" + "\n\n".join(extra_desc_parts)).strip()

        # ------------------------------------------------------------------
        # Requirements: pull from job_highlights qualification keys
        # ------------------------------------------------------------------
        requirements: list[str] = []
        for key in _HIGHLIGHTS_REQUIREMENT_KEYS:
            items = highlights.get(key)
            if items and isinstance(items, list):
                for item in items:
                    text = str(item).strip()
                    if text and text not in requirements:
                        requirements.append(text)

        try:
            return Lead(
                company=company,
                title=title,
                apply_url=apply_url,
                source=f"api:{self.name}",
                source_trust=self.trust,
                description=full_desc[:3000],
                requirements=requirements,
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
    if any(kw in blob for kw in ("customer success", "customer service", "customer support",
                                   "customer care", "chat support", "support agent",
                                   "support representative", "call center", "contact center")):
        return "customer-success"
    if any(kw in blob for kw in ("data entry", "virtual assistant", "administrative", "clerical")):
        return "admin"
    if any(kw in blob for kw in ("product manager", "product owner")):
        return "product"
    if any(kw in blob for kw in ("data scientist", "data analyst", "ml engineer", "machine learning")):
        return "data"
    if any(kw in blob for kw in ("finance", "accountant", "cfo", "bookkeep")):
        return "finance"
    return "remote-jobs"
