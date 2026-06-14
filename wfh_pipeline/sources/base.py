"""The LeadSource interface every lead adapter implements."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Lead, SourceTrust


class LeadSource(ABC):
    """A pluggable provider of job leads.

    Implementations are responsible for eligibility filtering: only rows whose
    ``verified`` column is truthy may be returned.

    Class attributes
    ----------------
    name : str
        Short identifier used in logs and the ``source`` field on leads.
    trust : SourceTrust
        ``"direct"`` for ATS feeds whose links go straight to employer sites;
        ``"aggregator"`` for RSS job-board feeds; ``"unknown"`` default.
        The pipeline uses this (alongside ``lead.link_type``) to route leads
        to the correct publishing lane.
    """

    name: str = "base"
    trust: SourceTrust = "unknown"

    @abstractmethod
    def fetch_new_leads(self) -> list[Lead]:
        """Return all eligible (verified) leads currently available."""
