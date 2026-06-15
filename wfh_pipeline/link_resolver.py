"""Optional best-effort resolver that upgrades aggregator URLs to direct ATS links.

Only activated when ``--resolve-source-links`` is passed on the CLI.  The
strategy is conservative: we only upgrade a URL when the resolved destination
is a *known-direct* ATS domain (as classified by :mod:`wfh_pipeline.link_classifier`).
Unknown destinations are left unchanged — we never silently swap in a link we
can't verify.

Usage from the pipeline::

    from wfh_pipeline.link_resolver import try_resolve_lead
    upgraded_lead = try_resolve_lead(lead)

``try_resolve_lead`` always returns a :class:`~wfh_pipeline.models.Lead`, either
the original or a ``model_copy``-upgraded one with the new ``apply_url``.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .link_classifier import classify_url

if TYPE_CHECKING:
    from .models import Lead

logger = logging.getLogger(__name__)

# How long to wait for each HTTP hop before giving up.
_TIMEOUT_SECONDS = 10
# Maximum number of redirects to follow when probing a URL.
_MAX_REDIRECTS = 5


def _final_url(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> str | None:
    """Follow redirects and return the final URL, or None on any error.

    Sends a HEAD request first (faster); falls back to GET if the server
    refuses HEAD with a 4xx/5xx.  Never reads the response body.
    """
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
        ) as client:
            try:
                resp = client.head(url)
                if resp.status_code < 400:
                    return str(resp.url)
                # Some servers don't support HEAD — fall back to GET
                resp = client.get(url)
            except httpx.TooManyRedirects:
                logger.debug("Too many redirects resolving %r", url)
                return None
            return str(resp.url)
    except httpx.HTTPError as exc:
        logger.debug("HTTP error resolving %r: %s", url, exc)
        return None


def resolve_apply_url(
    url: str,
    *,
    extra_direct: tuple[str, ...] = (),
    extra_aggregator: tuple[str, ...] = (),
) -> str | None:
    """Try to resolve ``url`` to a direct ATS link.

    Returns the resolved URL if (and only if) it classifies as ``"direct"``.
    Returns ``None`` when:

    * the URL is already direct (no upgrade needed)
    * the resolved destination is not a known-direct ATS domain
    * the HTTP request fails for any reason

    Parameters
    ----------
    url:
        The aggregator or unknown apply URL to probe.
    extra_direct:
        Additional direct domains to recognise (passed through to the classifier).
    extra_aggregator:
        Additional aggregator domains (passed through to the classifier).
    """
    # Skip if already direct — nothing to upgrade.
    if classify_url(url, extra_direct=extra_direct, extra_aggregator=extra_aggregator) == "direct":
        return None

    final = _final_url(url)
    if final is None or final == url:
        return None

    resolved_type = classify_url(
        final, extra_direct=extra_direct, extra_aggregator=extra_aggregator
    )
    if resolved_type == "direct":
        logger.info("Resolved %r → %r (direct ATS link)", url, final)
        return final

    logger.debug("Resolved %r → %r but destination is %r, not upgrading", url, final, resolved_type)
    return None


def try_resolve_lead(
    lead: "Lead",
    *,
    extra_direct: tuple[str, ...] = (),
    extra_aggregator: tuple[str, ...] = (),
) -> "Lead":
    """Return a copy of *lead* with an upgraded ``apply_url`` if resolution succeeds.

    If the URL cannot be upgraded (not resolvable, or destination is not a
    known-direct ATS domain), the original lead is returned unchanged.

    This function never raises — any error during resolution is logged at DEBUG
    level and the original lead is returned.
    """
    upgraded = resolve_apply_url(
        lead.apply_url,
        extra_direct=extra_direct,
        extra_aggregator=extra_aggregator,
    )
    if upgraded is None:
        return lead
    return lead.model_copy(update={"apply_url": upgraded})
