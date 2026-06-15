"""Google Sheets lead source (service-account auth via gspread)."""
from __future__ import annotations

import logging
from pathlib import Path

import gspread

from ..models import Lead
from .base import LeadSource
from .parsing import lead_from_record

logger = logging.getLogger(__name__)


class GoogleSheetsLeadSource(LeadSource):
    """Reads one worksheet whose first row is the header (same schema as the CSV).

    Trust defaults to ``"aggregator"`` because a spreadsheet can hold leads
    from any source, including job-board aggregators.  Override by subclassing
    and setting ``trust = "direct"`` if your sheet only contains ATS-direct URLs.
    """

    name = "sheets"
    trust = "aggregator"

    def __init__(
        self, sheet_id: str, worksheet: str, service_account_path: str | Path
    ) -> None:
        self._sheet_id = sheet_id
        self._worksheet = worksheet
        self._service_account_path = Path(service_account_path)

    def fetch_new_leads(self) -> list[Lead]:
        client = gspread.service_account(filename=str(self._service_account_path))
        spreadsheet = client.open_by_key(self._sheet_id)
        worksheet = spreadsheet.worksheet(self._worksheet)
        records = worksheet.get_all_records()
        leads = [
            lead
            for record in records
            if (lead := lead_from_record(
                record,
                source=f"sheets:{self._worksheet}",
                source_trust=self.trust,
            )) is not None
        ]
        verified = [lead for lead in leads if lead.verified]
        logger.info(
            "Sheet %s.../%s: %d rows, %d parsed, %d verified/eligible",
            self._sheet_id[:8],
            self._worksheet,
            len(records),
            len(leads),
            len(verified),
        )
        return verified
