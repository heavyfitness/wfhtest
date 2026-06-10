"""Repo-root conftest: makes wfh_pipeline importable and provides shared fixtures."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402

from wfh_pipeline.models import Lead  # noqa: E402

SAMPLE_CSV = REPO_ROOT / "sample_leads.csv"


@pytest.fixture
def make_lead():
    """Factory for a realistic verified lead; override any field via kwargs."""

    def _make(**overrides) -> Lead:
        defaults = dict(
            company="BrightDesk Solutions",
            title="Remote Customer Service Representative",
            pay="$17-$19/hr",
            remote=True,
            employment_type="FULL_TIME",
            requirements=[
                "2+ years customer service experience",
                "Quiet home office with reliable internet",
            ],
            description="Handle inbound calls and emails for a national home-warranty brand.",
            apply_url="https://example.com/jobs/brightdesk-customer-service",
            source="test",
            category="customer-service",
            verified=True,
        )
        defaults.update(overrides)
        return Lead(**defaults)

    return _make
