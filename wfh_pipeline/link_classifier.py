"""Classify a job-lead apply URL as direct, aggregator, or unknown.

A "direct" link goes to the employer's own site or a recognised ATS that hosts
their official job posting.  An "aggregator" link goes to a job-board or
middleman that re-lists jobs from many employers.

The WFH Connect pledges *direct apply links only* on job-lead posts.  This
module makes that pledge structurally enforceable: the generator and QC gate
both consult ``classify_url()`` to decide framing and to reject posts that
would otherwise misrepresent an aggregator link as direct.

Domain lists
------------
Two module-level frozensets — ``DIRECT_DOMAINS`` and ``AGGREGATOR_DOMAINS`` —
are the authoritative defaults.  Both accept globs in the form ``*.example.com``
(only a leading ``*.`` wildcard is supported; that covers all subdomains).

At runtime the pipeline passes ``extra_direct`` and ``extra_aggregator`` tuples
read from ``EXTRA_DIRECT_DOMAINS`` / ``EXTRA_AGGREGATOR_DOMAINS`` in ``.env``
(comma-separated) so you can extend the lists without touching code.

Classification logic
--------------------
1. Extract ``netloc`` from the URL.
2. Check aggregator list first (explicit block beats anything else).
3. Then check direct list (including ``*.`` subdomain globs).
4. If neither matches, return ``"unknown"``.

``LinkType = Literal["direct", "aggregator", "unknown"]``
"""
from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

LinkType = Literal["direct", "aggregator", "unknown"]

# ---------------------------------------------------------------------------
# Default domain lists
# ---------------------------------------------------------------------------

#: Domains / ATS hosts whose links are always direct employer links.
#: Use ``*.`` prefix to match any subdomain (e.g. ``*.myworkdayjobs.com``).
DIRECT_DOMAINS: frozenset[str] = frozenset(
    {
        # Major ATS platforms — every posting here is employer-hosted
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "jobs.lever.co",
        "apply.workable.com",
        "jobs.ashbyhq.com",
        "*.myworkdayjobs.com",
        "*.icims.com",
        "*.taleo.net",
        "*.successfactors.com",
        "*.successfactors.eu",
        "*.brassring.com",
        "*.smartrecruiters.com",
        "*.jobvite.com",
        "apply.recruitee.com",
        "*.recruitee.com",
        "*.bamboohr.com",
        "*.jazz.co",
        "boards.eu.greenhouse.io",
        "jobs.gusto.com",
        "careers.rippling.com",
    }
)

#: Domains that are aggregators / job boards — links here are NOT direct.
AGGREGATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "weworkremotely.com",
        "www.weworkremotely.com",
        "remoteok.com",
        "remoteok.io",
        "remotive.com",
        "remotive.io",
        "himalayas.app",
        "www.himalayas.app",
        "indeed.com",
        "www.indeed.com",
        "linkedin.com",
        "www.linkedin.com",
        "jobs.linkedin.com",
        "glassdoor.com",
        "www.glassdoor.com",
        "monster.com",
        "www.monster.com",
        "ziprecruiter.com",
        "www.ziprecruiter.com",
        "careerbuilder.com",
        "www.careerbuilder.com",
        "simplyhired.com",
        "www.simplyhired.com",
        "flexjobs.com",
        "www.flexjobs.com",
        "remote.co",
        "www.remote.co",
        "jobspresso.co",
        "www.jobspresso.co",
        "workingnomads.com",
        "www.workingnomads.com",
        "dynamitejobs.com",
        "www.dynamitejobs.com",
        "remoteleaf.com",
        "www.remoteleaf.com",
        "justremote.co",
        "www.justremote.co",
        "nodesk.co",
        "www.nodesk.co",
        "4dayweek.io",
        "www.4dayweek.io",
        "wellfound.com",
        "angel.co",
        "www.wellfound.com",
        "dice.com",
        "www.dice.com",
        "snagajob.com",
        "www.snagajob.com",
        "jooble.org",
        "www.jooble.org",
        "talroo.com",
        "www.talroo.com",
        "appcast.io",
        "www.appcast.io",
    }
)

# Pattern: leading "*."-style wildcard (only supported form)
_GLOB_RE = re.compile(r"^\*\.(.+)$")


def _matches(netloc: str, domain_set: frozenset[str]) -> bool:
    """Return True if *netloc* matches any entry in *domain_set*."""
    netloc = netloc.lower()
    for entry in domain_set:
        m = _GLOB_RE.match(entry)
        if m:
            suffix = m.group(1).lower()
            if netloc == suffix or netloc.endswith("." + suffix):
                return True
        else:
            if netloc == entry.lower():
                return True
    return False


def classify_url(
    url: str,
    *,
    extra_direct: tuple[str, ...] = (),
    extra_aggregator: tuple[str, ...] = (),
) -> LinkType:
    """Return ``"direct"``, ``"aggregator"``, or ``"unknown"`` for *url*.

    Parameters
    ----------
    url:
        The apply URL to classify.  Must be an absolute ``http(s)://`` URL;
        anything unparseable is classified as ``"unknown"``.
    extra_direct:
        Additional direct domains (from config / ``EXTRA_DIRECT_DOMAINS`` env var).
    extra_aggregator:
        Additional aggregator domains (from config / ``EXTRA_AGGREGATOR_DOMAINS``).
    """
    try:
        netloc = urlparse(url).netloc
    except Exception:
        return "unknown"
    if not netloc:
        return "unknown"

    effective_aggregator = AGGREGATOR_DOMAINS | frozenset(extra_aggregator)
    effective_direct = DIRECT_DOMAINS | frozenset(extra_direct)

    # Aggregator check wins if both lists somehow match (shouldn't happen, but be safe)
    if _matches(netloc, effective_aggregator):
        return "aggregator"
    if _matches(netloc, effective_direct):
        return "direct"
    return "unknown"


def is_direct_url(
    url: str,
    *,
    extra_direct: tuple[str, ...] = (),
    extra_aggregator: tuple[str, ...] = (),
) -> bool:
    """Convenience wrapper — True only when classified as ``"direct"``."""
    return classify_url(url, extra_direct=extra_direct, extra_aggregator=extra_aggregator) == "direct"
