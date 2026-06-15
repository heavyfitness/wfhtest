"""Lead relevance filter — keep entry-level / CS / call-center / data-entry remote roles.

Configured entirely via environment variables:

.. code-block:: dotenv

   # At least ONE must appear in title+description to keep the lead.
   RELEVANCE_INCLUDE_KEYWORDS=customer service,data entry,call center,...

   # If ANY appears in the *title*, the lead is rejected.
   RELEVANCE_EXCLUDE_TITLE=senior,staff,principal,...

   # Reject leads whose description states 3+ years of experience required.
   # Set to false to disable the experience-years check.
   RELEVANCE_REJECT_EXPERIENCE_YEARS=true

   # When True, a lead with NO include-keyword match is still KEPT.
   RELEVANCE_PERMISSIVE=false

Leave any var blank to use built-in defaults.
"""
from __future__ import annotations

import logging
import os
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Experience-years rejection regex
# ---------------------------------------------------------------------------
# Matches patterns like: "3+ years", "5 years", "minimum of 4 years",
# "at least 3 years", "7+ years of experience", "3 to 5 years"
# Only rejects when the number is 3 or higher.

_YOE_PATTERN = re.compile(
    r"""
    (?:
        (?:minimum\s+of\s+|at\s+least\s+)?   # optional prefix
        (\d+)\+?\s*(?:to\s+\d+\s*)?           # number (e.g. "3", "3+", "3 to 5")
        \s*years?\s*                            # "years" or "year"
        (?:of\s+)?                             # optional "of"
        (?:experience|exp\.?|relevant|work)?   # optional qualifier
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

_MIN_YEARS_THRESHOLD = 3  # reject leads requiring this many or more years


def _min_years_required(text: str) -> int | None:
    """Return the highest years-of-experience requirement found in *text*, or None.

    Scans the text for experience-years patterns and returns the maximum
    number found, so callers can reject based on a threshold.
    """
    max_years: int | None = None
    for match in _YOE_PATTERN.finditer(text):
        try:
            years = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if max_years is None or years > max_years:
            max_years = years
    return max_years


# ---------------------------------------------------------------------------
# Default keyword lists — tuned for WFH Connect's audience
# (entry-level, phone + non-phone CS, data entry, call center)
# ---------------------------------------------------------------------------

_DEFAULT_INCLUDE = (
    # Core audience role types — phone and non-phone both welcome
    "customer service",
    "customer support",
    "customer care",
    "customer experience",
    "customer success",
    "client support",
    "client services",
    "client care",
    "call center",
    "contact center",
    "inbound",
    "outbound",
    "data entry",
    "chat support",
    "chat agent",
    "email support",
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
    "back office",
    # Entry-level signals
    "no experience",
    "no experience required",
    "entry level",
    "entry-level",
    "junior",
    "no degree",
    "associate",
    "representative",
    "agent",
)

_DEFAULT_EXCLUDE_TITLE = (
    # Seniority / scope signals that put a role out of audience
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
    " engineer",
    "engineer ",
    " developer",
    "developer ",
    " analyst",
    "analyst ",
    "scientist",
    " researcher",
    "researcher",
    "experienced ",
    " experienced",
    # Function areas outside audience scope
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
    # Placeholder listings — never post
    "talent community",
    "join our team",
)


def _parse_kw_list(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    return tuple(kw.strip().lower() for kw in raw.split(",") if kw.strip())


def load_relevance_config() -> tuple[tuple[str, ...], tuple[str, ...], bool, bool]:
    """Load config from environment.

    Returns ``(include_keywords, exclude_title_keywords, permissive, reject_experience_years)``.
    """
    raw_include = os.environ.get("RELEVANCE_INCLUDE_KEYWORDS", "")
    raw_exclude = os.environ.get("RELEVANCE_EXCLUDE_TITLE", "")
    permissive = os.environ.get("RELEVANCE_PERMISSIVE", "false").lower() in ("1", "true", "yes")
    reject_yoe = os.environ.get("RELEVANCE_REJECT_EXPERIENCE_YEARS", "true").lower() not in ("0", "false", "no")

    include = _parse_kw_list(raw_include) if raw_include else _DEFAULT_INCLUDE
    exclude = _parse_kw_list(raw_exclude) if raw_exclude else _DEFAULT_EXCLUDE_TITLE
    return include, exclude, permissive, reject_yoe


class RelevanceFilter:
    """Keyword + experience-years gate applied before LLM generation.

    Parameters
    ----------
    include_keywords:
        At least one must appear in title+description to keep the lead.
        Empty tuple = no requirement.
    exclude_title_keywords:
        Any match in the **title** rejects the lead immediately.
    permissive:
        When True, no include-keyword match is still kept.
    reject_experience_years:
        When True (default), leads whose description states 3+ years of
        experience required are rejected regardless of title.
    """

    def __init__(
        self,
        include_keywords: tuple[str, ...] = (),
        exclude_title_keywords: tuple[str, ...] = (),
        *,
        permissive: bool = False,
        reject_experience_years: bool = True,
    ) -> None:
        self._include = include_keywords
        self._exclude = exclude_title_keywords
        self._permissive = permissive
        self._reject_yoe = reject_experience_years

    @classmethod
    def from_env(cls) -> "RelevanceFilter":
        """Build from environment variables."""
        include, exclude, permissive, reject_yoe = load_relevance_config()
        return cls(include, exclude, permissive=permissive, reject_experience_years=reject_yoe)

    def is_relevant(self, title: str, description: str = "") -> bool:
        """Return True if the lead should be kept.

        Rejection order:
        1. Title exclude keyword match → reject.
        2. Description states 3+ years of experience required → reject.
        3. No include keyword found (strict mode) → reject.
        """
        title_lower = title.lower()
        body_lower = (title + " " + description).lower()

        # Step 1: title exclude (hard stop)
        for kw in self._exclude:
            if kw in title_lower:
                logger.debug("Relevance filter: title exclude %r matched %r", kw, title)
                return False

        # Step 2: experience-years check on description
        if self._reject_yoe and description:
            max_yoe = _min_years_required(description)
            if max_yoe is not None and max_yoe >= _MIN_YEARS_THRESHOLD:
                logger.debug(
                    "Relevance filter: %d+ years experience required in %r — rejecting",
                    max_yoe, title,
                )
                return False

        # Step 3: include keyword requirement
        if self._include and not self._permissive:
            if not any(kw in body_lower for kw in self._include):
                return False

        return True

    def filter_leads(self, leads: list) -> list:
        """Return leads passing the relevance check, logging rejects."""
        kept = []
        skipped = 0
        for lead in leads:
            if self.is_relevant(lead.title, lead.description):
                kept.append(lead)
            else:
                skipped += 1
                logger.debug("Relevance filter: skipping %r (%s)", lead.title, lead.company)
        if skipped:
            logger.info(
                "Relevance filter: kept %d / %d leads (%d skipped)",
                len(kept), len(leads), skipped,
            )
        return kept
