"""Local CSV lead source — mirrors the Google Sheet schema; used for testing."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..models import Lead
from .base import LeadSource
from .parsing import lead_from_record

logger = logging.getLogger(__name__)


class CSVLeadSource(LeadSource):
    name = "csv"

    def __init__(self, csv_path: str | Path) -> None:
        self._path = Path(csv_path)

    def fetch_new_leads(self) -> list[Lead]:
        if not self._path.exists():
            raise FileNotFoundError(f"Lead CSV not found: {self._path}")
        frame = pd.read_csv(self._path, dtype=str, keep_default_na=False)
        records = frame.to_dict(orient="records")
        leads = [
            lead
            for record in records
            if (lead := lead_from_record(record, source=f"csv:{self._path.name}")) is not None
        ]
        verified = [lead for lead in leads if lead.verified]
        logger.info(
            "CSV %s: %d rows, %d parsed, %d verified/eligible",
            self._path.name,
            len(records),
            len(leads),
            len(verified),
        )
        return verified
