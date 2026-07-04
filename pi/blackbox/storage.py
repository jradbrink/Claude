"""Local SQLite buffer — the offline-first source of truth on the device.

Rows are stored as JSON payloads in exactly the shape the Supabase tables
expect, so sync is a dumb upsert and the local schema never drifts from the
cloud schema. Rows are flagged synced only after the cloud confirms them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .trip import TripSummary

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    synced     INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS cold_events (
    id      TEXT PRIMARY KEY,
    trip_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    synced  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS trips_unsynced_idx ON trips (synced);
"""


class Storage:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # The sync worker runs on a background thread; one connection guarded
        # by a lock is plenty at this write volume.
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def save_trip(self, summary: TripSummary) -> None:
        """Persist a finished trip and its violation events atomically."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO trips (id, payload, synced) VALUES (?, ?, 0)",
                (summary.id, json.dumps(summary.as_row())),
            )
            for event in summary.event_rows():
                self._db.execute(
                    "INSERT OR REPLACE INTO cold_events (id, trip_id, payload, synced)"
                    " VALUES (?, ?, ?, 0)",
                    (event["id"], summary.id, json.dumps(event)),
                )

    def unsynced_trips(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM trips WHERE synced = 0 ORDER BY created_at"
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def unsynced_events(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM cold_events WHERE synced = 0"
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def mark_trips_synced(self, ids: list[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE trips SET synced = 1 WHERE id = ?", [(i,) for i in ids]
            )

    def mark_events_synced(self, ids: list[str]) -> None:
        with self._lock, self._db:
            self._db.executemany(
                "UPDATE cold_events SET synced = 1 WHERE id = ?", [(i,) for i in ids]
            )

    def all_trips(self) -> list[dict]:
        """Every stored trip, for report generation straight off the device."""
        with self._lock:
            rows = self._db.execute("SELECT payload FROM trips ORDER BY created_at").fetchall()
        return [json.loads(r[0]) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
