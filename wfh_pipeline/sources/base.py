"""The LeadSource interface every lead adapter implements."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Lead


class LeadSource(ABC):
    """A pluggable provider of job leads.

    Implementations are responsible for eligibility filtering: only rows whose
    ``verified`` column is truthy may be returned.
    """

    name: str = "base"

    @abstractmethod
    def fetch_new_leads(self) -> list[Lead]:
        """Return all eligible (verified) leads currently available."""
