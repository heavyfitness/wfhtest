"""SQLite-backed record of leads that have already been published (dedup)."""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posted (
    lead_hash  TEXT PRIMARY KEY,
    wp_post_id INTEGER,
    url        TEXT,
    posted_at  TEXT NOT NULL
)
"""


class PostedStore:
    """Keyed by the stable lead hash; a lead in here is never posted again."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        if self._path.parent != Path("."):
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        with self._conn:
            self._conn.execute(_SCHEMA)

    def is_posted(self, lead_hash: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM posted WHERE lead_hash = ?", (lead_hash,)
        ).fetchone()
        return row is not None

    def record(self, lead_hash: str, wp_post_id: int | None, url: str) -> None:
        posted_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO posted (lead_hash, wp_post_id, url, posted_at) "
                "VALUES (?, ?, ?, ?)",
                (lead_hash, wp_post_id, url, posted_at),
            )
        logger.debug("Recorded lead %s -> WP post %s", lead_hash, wp_post_id)

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM posted").fetchone()[0])

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PostedStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
