"""Pluggable lead sources.

GoogleSheetsLeadSource is intentionally not re-exported here so importing the
package never requires gspread; import it from wfh_pipeline.sources.sheets.
"""
from .base import LeadSource
from .csv_source import CSVLeadSource
from .parsing import lead_from_record

__all__ = ["LeadSource", "CSVLeadSource", "lead_from_record"]
