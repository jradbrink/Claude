"""Supabase sync — idempotent upserts over PostgREST.

Trips are pushed before cold_events so the foreign key always holds. Client
UUIDs plus `resolution=merge-duplicates` make retries safe: a sync that died
halfway simply re-upserts the same rows next time.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .storage import Storage

log = logging.getLogger(__name__)


class SupabaseSync:
    def __init__(self, cfg, storage: Storage, device_id: str) -> None:
        self.base = cfg.url.rstrip("/")
        self.timeout = cfg.timeout_s
        self.storage = storage
        self.device_id = device_id
        self._headers = {
            "apikey": cfg.service_key,
            "Authorization": f"Bearer {cfg.service_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }

    def sync_once(self) -> bool:
        """Push everything unsynced. Returns True if nothing is left pending."""
        try:
            trips = self.storage.unsynced_trips()
            if trips and not self._upsert("trips", trips):
                return False
            if trips:
                self.storage.mark_trips_synced([t["id"] for t in trips])
                log.info("Synced %d trip(s)", len(trips))

            events = self.storage.unsynced_events()
            if events and not self._upsert("cold_events", events):
                return False
            if events:
                self.storage.mark_events_synced([e["id"] for e in events])
                log.info("Synced %d cold event(s)", len(events))

            self._heartbeat()
            return True
        except requests.RequestException as exc:
            log.warning("Sync failed (will retry): %s", exc)
            return False

    def _upsert(self, table: str, rows: list[dict]) -> bool:
        resp = requests.post(
            f"{self.base}/rest/v1/{table}",
            params={"on_conflict": "id"},
            headers=self._headers,
            json=rows,
            timeout=self.timeout,
        )
        if resp.status_code >= 300:
            log.warning("Upsert %s -> HTTP %d: %s", table, resp.status_code, resp.text[:300])
            return False
        return True

    def _heartbeat(self) -> None:
        """Best-effort device health ping; failure never fails the sync."""
        try:
            requests.patch(
                f"{self.base}/rest/v1/devices",
                params={"id": f"eq.{self.device_id}"},
                headers=self._headers,
                json={"last_seen_at": datetime.now(timezone.utc).isoformat()},
                timeout=self.timeout,
            )
        except requests.RequestException:
            pass
