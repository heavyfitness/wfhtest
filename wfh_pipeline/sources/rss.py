"""RSS/JSON feed lead sources — aggregator trust lane.

All sources here set ``source_trust = "aggregator"``.  Their apply_url links
point to job-board listing pages (remoteok.com, weworkremotely.com, etc.) and
will be classified as ``link_type="aggregator"`` by the URL classifier, which
means they publish as drafts by default and receive honest "view on {Board}"
labeling in generated copy.

To upgrade an aggregator lead to a direct-apply link, run the pipeline with
``--resolve-source-links`` which attempts to follow the board URL through to
the employer's real ATS application page.

Sources implemented here
------------------------
* :class:`RemoteOKLeadSource`    — https://remoteok.com/remote-jobs.rss
* :class:`WeWorkRemotelyLeadSource` — https://weworkremotely.com/remote-jobs.rss
* :class:`RemotiveLeadSource`    — https://remotive.com/api/remote-jobs (JSON API)
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date

import feedparser
import httpx

from ..models import Lead
from .base import LeadSource

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and unescape entities; collapse whitespace."""
    return re.sub(r"\s+", " ", _TAG_RE.sub("", html.unescape(text or ""))).strip()


def _parsed_to_date(struct_time) -> date:
    """Convert feedparser's time.struct_time to a date, falling back to today."""
    if struct_time is None:
        return date.today()
    try:
        return date(struct_time.tm_year, struct_time.tm_mon, struct_time.tm_mday)
    except Exception:
        return date.today()


def _category_from_tags(tags: list) -> str:
    """Best-effort WFH-friendly category from a list of feedparser tag dicts."""
    CATEGORY_MAP = {
        "engineering": "engineering",
        "developer": "engineering",
        "devops": "engineering",
        "design": "design",
        "marketing": "marketing",
        "sales": "sales",
        "customer": "customer-success",
        "support": "customer-success",
        "product": "product",
        "finance": "finance",
        "data": "data",
        "writing": "content",
        "content": "content",
    }
    for tag in tags:
        term = str(tag.get("term", "")).lower()
        for kw, cat in CATEGORY_MAP.items():
            if kw in term:
                return cat
    return "remote-jobs"


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class RSSLeadSource(LeadSource):
    """Abstract base for RSS/Atom feed sources.

    Concrete subclasses implement :meth:`_parse_entry` to convert a single
    feedparser entry dict into a :class:`~wfh_pipeline.models.Lead` (or
    ``None`` to skip).
    """

    trust = "aggregator"
    feed_url: str = ""

    def __init__(self, feed_url: str | None = None) -> None:
        if feed_url is not None:
            self.feed_url = feed_url

    def fetch_new_leads(self) -> list[Lead]:
        logger.info("Fetching RSS feed: %s", self.feed_url)
        parsed = feedparser.parse(self.feed_url)
        entries = parsed.get("entries", [])
        if not entries:
            logger.warning(
                "%s: no entries returned (feed status=%s)",
                self.name,
                parsed.get("status", "unknown"),
            )
            return []
        leads: list[Lead] = []
        for entry in entries:
            try:
                lead = self._parse_entry(entry)
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s: skipping malformed entry: %s", self.name, exc)
                lead = None
            if lead is not None:
                leads.append(lead)
        logger.info("%s: %d entries, %d leads", self.name, len(entries), len(leads))
        return leads

    def _parse_entry(self, entry: dict) -> Lead | None:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# RemoteOK
# ---------------------------------------------------------------------------


class RemoteOKLeadSource(RSSLeadSource):
    """Leads from https://remoteok.com/remote-jobs.rss

    RemoteOK entries include a ``company`` field directly; tags map to the
    job categories they publish.
    """

    name = "remoteok"
    feed_url = "https://remoteok.com/remote-jobs.rss"

    def _parse_entry(self, entry: dict) -> Lead | None:
        title: str = _strip_html(entry.get("title", "")).strip()
        company: str = _strip_html(entry.get("company", "")).strip()
        link: str = entry.get("link", "").strip()

        if not title or not company or not link:
            return None
        # Skip the first RSS entry which RemoteOK uses as a header/ad
        if "remoteok" in title.lower() and "remote ok" in company.lower():
            return None

        tags = entry.get("tags", [])
        description = _strip_html(entry.get("summary", ""))[:2000]
        found = _parsed_to_date(entry.get("published_parsed"))

        return Lead(
            company=company,
            title=title,
            apply_url=link,
            source=f"rss:{self.name}",
            source_trust=self.trust,
            description=description,
            category=_category_from_tags(tags),
            date_found=found,
            remote=True,
            verified=True,
        )


# ---------------------------------------------------------------------------
# We Work Remotely
# ---------------------------------------------------------------------------


class WeWorkRemotelyLeadSource(RSSLeadSource):
    """Leads from https://weworkremotely.com/remote-jobs.rss

    WWR encodes the company name in the title as "Company: Role Title".
    """

    name = "weworkremotely"
    feed_url = "https://weworkremotely.com/remote-jobs.rss"

    def _parse_entry(self, entry: dict) -> Lead | None:
        raw_title: str = _strip_html(entry.get("title", "")).strip()
        link: str = entry.get("link", "").strip()

        if not raw_title or not link:
            return None

        # "Company Name: Job Title" format (WWR standard)
        if ":" in raw_title:
            company_part, title_part = raw_title.split(":", 1)
            company = company_part.strip()
            title = title_part.strip()
        else:
            company = "Unknown"
            title = raw_title

        if not company or not title:
            return None

        tags = entry.get("tags", [])
        description = _strip_html(entry.get("summary", ""))[:2000]
        found = _parsed_to_date(entry.get("published_parsed"))

        return Lead(
            company=company,
            title=title,
            apply_url=link,
            source=f"rss:{self.name}",
            source_trust=self.trust,
            description=description,
            category=_category_from_tags(tags),
            date_found=found,
            remote=True,
            verified=True,
        )


# ---------------------------------------------------------------------------
# Remotive (JSON API — not RSS, but same purpose)
# ---------------------------------------------------------------------------


class RemotiveLeadSource(LeadSource):
    """Leads from https://remotive.com/api/remote-jobs (JSON API).

    Unlike the other two sources here, Remotive's public API returns JSON.
    The interface is otherwise identical to the RSS sources.

    Parameters
    ----------
    category:
        Optional Remotive job category slug (e.g. ``"software-dev"``) to
        filter results.  Defaults to all categories.
    limit:
        Max jobs to fetch per run.  Defaults to 50.
    """

    name = "remotive"
    trust = "aggregator"

    _API_URL = "https://remotive.com/api/remote-jobs"

    _CATEGORY_MAP = {
        "software-dev": "engineering",
        "devops-sysadmin": "engineering",
        "design": "design",
        "marketing": "marketing",
        "sales": "sales",
        "customer-support": "customer-success",
        "product": "product",
        "finance-legal": "finance",
        "data": "data",
        "writing": "content",
        "qa": "engineering",
    }

    def __init__(
        self,
        *,
        category: str | None = None,
        limit: int = 50,
    ) -> None:
        self._category = category
        self._limit = limit

    def fetch_new_leads(self) -> list[Lead]:
        params: dict = {"limit": self._limit}
        if self._category:
            params["category"] = self._category
        logger.info("Fetching Remotive API (category=%s, limit=%d)", self._category, self._limit)
        try:
            with httpx.Client(timeout=20) as client:
                resp = client.get(self._API_URL, params=params)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("Remotive API error: %s", exc)
            return []

        data = resp.json()
        jobs = data.get("jobs", [])
        leads: list[Lead] = []
        for job in jobs:
            lead = self._job_to_lead(job)
            if lead is not None:
                leads.append(lead)
        logger.info("Remotive: %d jobs, %d leads", len(jobs), len(leads))
        return leads

    def _job_to_lead(self, job: dict) -> Lead | None:
        title = str(job.get("title", "")).strip()
        company = str(job.get("company_name", "")).strip()
        url = str(job.get("url", "")).strip()
        if not title or not company or not url:
            return None

        raw_cat = str(job.get("category", "")).lower().replace(" & ", "-").replace(" ", "-")
        category = self._CATEGORY_MAP.get(raw_cat, "remote-jobs")

        raw_date = job.get("publication_date", "")
        try:
            found = date.fromisoformat(raw_date[:10]) if raw_date else date.today()
        except ValueError:
            found = date.today()

        description = _strip_html(str(job.get("description", "")))[:2000]

        try:
            return Lead(
                company=company,
                title=title,
                apply_url=url,
                source=f"api:{self.name}",
                source_trust=self.trust,
                description=description,
                category=category,
                date_found=found,
                remote=True,
                verified=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Remotive: skipping %r (%s): %s", title, company, exc)
            return None
