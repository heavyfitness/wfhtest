"""Multi-source lead aggregator.

:class:`MultiLeadSource` wraps any number of :class:`~wfh_pipeline.sources.base.LeadSource`
instances and returns a deduplicated union of their leads in a single
:meth:`fetch_new_leads` call.

The dedup here is *within-run* only (same apply_url / id from two sources in
the same batch).  Cross-run deduplication is handled upstream by the
:class:`~wfh_pipeline.state.PostedStore`.

Typical usage (built by :func:`build_sources`)::

    from wfh_pipeline.sources.multi import MultiLeadSource, build_sources
    from wfh_pipeline.config import Settings

    sources = build_sources(Settings())
    pipeline = Pipeline(source=sources, ...)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..models import Lead
from .base import LeadSource

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class MultiLeadSource(LeadSource):
    """Aggregates multiple lead sources into one, deduplicating within a run.

    Parameters
    ----------
    sources:
        Ordered list of :class:`LeadSource` instances.  All are queried on
        each :meth:`fetch_new_leads` call.
    name:
        Optional display name.
    """

    trust = "unknown"  # mixed; individual leads carry their own source_trust

    def __init__(self, sources: list[LeadSource], *, name: str = "multi") -> None:
        self._sources = list(sources)
        self.name = name

    def fetch_new_leads(self) -> list[Lead]:
        seen_ids: set[str] = set()
        all_leads: list[Lead] = []
        for source in self._sources:
            try:
                leads = source.fetch_new_leads()
            except Exception as exc:  # noqa: BLE001
                logger.error("Source %r raised an error: %s", source.name, exc)
                leads = []
            for lead in leads:
                if lead.id not in seen_ids:
                    seen_ids.add(lead.id)
                    all_leads.append(lead)
                else:
                    logger.debug(
                        "MultiLeadSource: dropping duplicate lead %s (%s)",
                        lead.id,
                        lead.title,
                    )
        logger.info(
            "MultiLeadSource[%s]: %d unique leads from %d source(s)",
            self.name,
            len(all_leads),
            len(self._sources),
        )
        return all_leads

    @property
    def sources(self) -> list[LeadSource]:
        """Read-only view of the child sources."""
        return list(self._sources)


# ---------------------------------------------------------------------------
# Source factory — builds sources from Settings
# ---------------------------------------------------------------------------


def build_sources(settings: "object", *, source_group: str = "all") -> "MultiLeadSource":
    """Build a :class:`MultiLeadSource` from a :class:`~wfh_pipeline.config.Settings` instance.

    Parameters
    ----------
    settings:
        Loaded ``Settings`` object (from :func:`~wfh_pipeline.config.Settings.load`).
    source_group:
        Which sources to include:

        * ``"direct"``     — ATS boards only (Greenhouse + Lever; direct lane)
        * ``"aggregator"`` — RSS feeds only (aggregator lane)
        * ``"all"``        — everything enabled in boards.yaml + .env (default)

    Returns a :class:`MultiLeadSource` ready to pass to :class:`~wfh_pipeline.pipeline.Pipeline`.
    """
    from .greenhouse import GreenhouseLeadSource
    from .job_api import JobAPILeadSource
    from .lever import LeverLeadSource
    from .rss import RemoteOKLeadSource, RemotiveLeadSource, WeWorkRemotelyLeadSource

    sources: list[LeadSource] = []

    # ------------------------------------------------------------------
    # ATS / direct sources
    # ------------------------------------------------------------------
    include_direct = source_group in ("direct", "all")
    if include_direct:
        for token in settings.greenhouse_tokens:
            sources.append(GreenhouseLeadSource(token))
        for slug in settings.lever_slugs:
            sources.append(LeverLeadSource(slug))

    # ------------------------------------------------------------------
    # RSS / aggregator sources
    # ------------------------------------------------------------------
    include_aggregator = source_group in ("aggregator", "all")
    if include_aggregator:
        if settings.rss_remoteok:
            sources.append(RemoteOKLeadSource())
        if settings.rss_weworkremotely:
            sources.append(WeWorkRemotelyLeadSource())
        if settings.rss_remotive:
            sources.append(RemotiveLeadSource())

    # ------------------------------------------------------------------
    # Optional job API — multi-query, entry-level focused
    # ------------------------------------------------------------------
    if (include_direct or include_aggregator) and settings.enable_job_api:
        if not settings.job_api_key:
            logger.warning(
                "ENABLE_JOB_API=true but JOB_API_KEY is not set — skipping job API source"
            )
        else:
            import os
            # queries comes from settings (parsed from JOB_API_QUERIES in .env)
            queries = list(settings.job_api_queries) if settings.job_api_queries else [
                "remote customer service no experience"
            ]
            max_per_query = int(os.environ.get("JOB_API_MAX_RESULTS", "20"))
            logger.info(
                "build_sources: wiring JobAPILeadSource with %d queries (max %d each)",
                len(queries), max_per_query,
            )
            sources.append(
                JobAPILeadSource(
                    settings.job_api_key,
                    queries=queries,
                    max_results_per_query=max_per_query,
                )
            )

    if not sources:
        logger.warning(
            "build_sources(%r): no sources configured — check boards.yaml and .env",
            source_group,
        )

    return MultiLeadSource(sources, name=source_group)
