"""Unit tests for stable lead IDs and the SQLite posted-store."""
from __future__ import annotations

from pathlib import Path

from wfh_pipeline.models import Lead, stable_lead_id
from wfh_pipeline.state import PostedStore


def test_stable_id_derived_from_apply_url(make_lead) -> None:
    lead_a = make_lead()
    lead_b = make_lead()
    assert lead_a.id == lead_b.id == stable_lead_id(lead_a.apply_url)
    other = make_lead(apply_url="https://example.com/jobs/other")
    assert other.id != lead_a.id


def test_stable_id_normalizes_case_and_trailing_slash() -> None:
    assert stable_lead_id("https://Example.com/jobs/X/") == stable_lead_id(
        "https://example.com/jobs/x"
    )


def test_explicit_id_preserved(make_lead) -> None:
    lead = make_lead(id="custom-id-123")
    assert lead.id == "custom-id-123"


def test_posted_store_roundtrip(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    with PostedStore(db_path) as store:
        assert not store.is_posted("abc123")
        store.record("abc123", 42, "https://thewfhconnect.com/?p=42")
        assert store.is_posted("abc123")
        assert store.count() == 1

    # Persists across connections.
    with PostedStore(db_path) as store:
        assert store.is_posted("abc123")
        assert not store.is_posted("def456")


def test_record_is_idempotent(tmp_path: Path) -> None:
    with PostedStore(tmp_path / "state.db") as store:
        store.record("abc123", 42, "url")
        store.record("abc123", 42, "url")
        assert store.count() == 1
