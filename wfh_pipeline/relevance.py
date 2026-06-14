"""Lead relevance filter — keep entry-level / CS / data-entry remote roles.

Configured entirely via environment variables so no code change is needed to
tune the filter:

.. code-block:: dotenv

   # At least ONE of these words must appear in the job title or description
   # to keep the lead.  Comma-separated, case-insensitive.
   RELEVANCE_INCLUDE_KEYWORDS=customer service,data entry,chat support,virtual assistant,remote support,customer care,customer success,customer experience,support specialist,support representative,support agent,client support,help desk,service representative,client services,client care,no experience,entry level,entry-level

   # If ANY of these appear in the *title*, the lead is rejected.
   # Comma-separated, case-insensitive.
   RELEVANCE_EXCLUDE_TITLE=senior,staff,principal,lead ,director,manager,vp ,vice president,chief,architect,scientist,engineer,developer,analyst,scientist,researcher,intern,security,devops,sre ,infrastructure,machine learning,data scientist,legal,finance,accounting,recruiter,talent acquisition,hr ,human resources,marketing,design,designer,ux ,ui ,product manager,program manager,creative,copywriter,seo,growth,operations manager

   # When True, a lead with NO include-keyword match is still KEPT.
   # Set to false (default) to require at least one include keyword.
   RELEVANCE_PERMISSIVE=false

Set RELEVANCE_INCLUDE_KEYWORDS= (empty) and RELEVANCE_EXCLUDE_TITLE= (empty)
to disable filtering entirely (pass-all mode).

The filter is applied per lead *before* content generation, so it saves LLM
cost when a board emits many roles outside your target audience.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sensible defaults — can be overridden 100% via .env
# ---------------------------------------------------------------------------

_DEFAULT_INCLUDE = (
    "customer service",
    "customer support",
    "customer care",
    "customer experience",
    "customer success",
    "client support",
    "client services",
    "client care",
    "data entry",
    "chat support",
    "chat agent",
    "virtual assistant",
    "remote support",
    "support specialist",
    "support representative",
    "support agent",
    "support associate",
    "service representative",
    "service agent",
    "service associate",
    "help desk",
    "helpdesk",
    "no experience",
    "entry level",
    "entry-level",
    "non-phone",
    "work from home",
    "work-from-home",
    "remote agent",
    "remote representative",
    "remote associate",
    "talent community",  # always exclude placeholder listings
)

_DEFAULT_EXCLUDE_TITLE = (
    "senior",
    "staff ",
    "principal",
    "lead ",
    " lead",
    "director",
    " manager",
    "manager ",
    "vp ",
    "vice president",
    "chief",
    "architect",
    "scientist",
    " engineer",
    "engineer ",
    " developer",
    "developer ",
    " analyst",
    "analyst ",
    " researcher",
    "researcher",
    "intern",
    " security",
    "devops",
    " sre",
    "infrastructure",
    "machine learning",
    "data scientist",
    "legal",
    " finance",
    "accounting",
    "recruiter",
    "talent acquisition",
    " hr ",
    "human resources",
    " marketing",
    " design",
    "designer",
    " ux ",
    " ui ",
    "product manager",
    "program manager",
    "copywriter",
    "growth ",
    "operations manager",
    "talent community",  # placeholder listing — never post
)


def _parse_kw_list(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    return tuple(kw.strip().lower() for kw in raw.split(",") if kw.strip())


def load_relevance_config() -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Load include/exclude keyword lists and permissive flag from environment.

    Returns ``(include_keywords, exclude_title_keywords, permissive)``.
    """
    raw_include = os.environ.get("RELEVANCE_INCLUDE_KEYWORDS", "")
    raw_exclude = os.environ.get("RELEVANCE_EXCLUDE_TITLE", "")
    permissive = os.environ.get("RELEVANCE_PERMISSIVE", "false").lower() in ("1", "true", "yes")

    include = _parse_kw_list(raw_include) if raw_include else _DEFAULT_INCLUDE
    exclude = _parse_kw_list(raw_exclude) if raw_exclude else _DEFAULT_EXCLUDE_TITLE
    return include, exclude, permissive


class RelevanceFilter:
    """Stateless callable that accepts or rejects a lead based on keyword rules.

    Parameters
    ----------
    include_keywords:
        If non-empty, at least one keyword must appear in ``title + description``
        for the lead to be kept.  Empty tuple = no inclusion requirement.
    exclude_title_keywords:
        If any keyword appears in the **title**, the lead is rejected regardless
        of include matches.  Empty tuple = no exclusion.
    permissive:
        When ``True``, a lead that matches no include keyword is still kept
        (only the exclude list applies).  Default ``False``.
    """

    def __init__(
        self,
        include_keywords: tuple[str, ...] = (),
        exclude_title_keywords: tuple[str, ...] = (),
        *,
        permissive: bool = False,
    ) -> None:
        self._include = include_keywords
        self._exclude = exclude_title_keywords
        self._permissive = permissive

    @classmethod
    def from_env(cls) -> "RelevanceFilter":
        """Load config from environment variables and return a ready filter."""
        include, exclude, permissive = load_relevance_config()
        return cls(include, exclude, permissive=permissive)

    def is_relevant(self, title: str, description: str = "") -> bool:
        """Return True if the lead should be kept, False if it should be skipped."""
        title_lower = title.lower()
        body_lower = (title + " " + description).lower()

        # Step 1: exclude filter on title (hard stop)
        for kw in self._exclude:
            if kw in title_lower:
                return False

        # Step 2: include filter on title + description
        if self._include and not self._permissive:
            if not any(kw in body_lower for kw in self._include):
                return False

        return True

    def filter_leads(self, leads: list) -> list:
        """Return only leads that pass the relevance check, logging rejects."""
        kept = []
        skipped = 0
        for lead in leads:
            if self.is_relevant(lead.title, lead.description):
                kept.append(lead)
            else:
                skipped += 1
                logger.debug(
                    "Relevance filter: skipping %r (%s)", lead.title, lead.company
                )
        if skipped:
            logger.info(
                "Relevance filter: kept %d / %d leads (%d skipped)",
                len(kept), len(leads), skipped,
            )
        return kept
