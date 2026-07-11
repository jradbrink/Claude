"""Push notifications to the owner's phone via ntfy.

ntfy (ntfy.sh or self-hosted) needs no account and no app-server: the phone
app subscribes to a topic, the Pi POSTs to the same topic. The topic name is
the only secret — use a long random string (anyone who knows it can read the
notifications), or point ntfy_url at a self-hosted instance with auth.

Timing caveat: the Pi only has network in the garage (WiFi), so this is a
*summary* channel, not a realtime one. With ignition-switched power the
normal drive ends with a power cut, and the summary for that drive goes out
on the next boot instead (journal-recovery path). Realtime in-car feedback
is the buzzer's job.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

import requests

from .trip import TripSummary

log = logging.getLogger(__name__)


def format_trip_message(summary: TripSummary, recovered: bool = False) -> dict:
    """ntfy title/body/priority/tags for a finished trip. Pure function."""
    minutes = round(summary.duration_s / 60)
    parts = [f"{minutes} min", f"{summary.distance_km_est:.1f} km (est)"]

    if summary.warmed_up and summary.warmup_s is not None:
        warm = f"uppvärmd efter {round(summary.warmup_s / 60)} min"
        if summary.oil_warmup_s is not None and summary.coolant_warmup_s is not None:
            warm += (
                f" (kylvätska {round(summary.coolant_warmup_s / 60)},"
                f" olja {round(summary.oil_warmup_s / 60)})"
            )
        parts.append(warm)
    else:
        parts.append("nådde aldrig arbetstemperatur")

    parts.append(f"max {summary.max_rpm} rpm")

    n = summary.cold_violation_count
    if n == 0:
        parts.append("inga kallstartsöverträdelser")
        title = "Körning klar ✔"
        priority = "default"
        tags = "white_check_mark"
    else:
        parts.append(f"{n} kallstartsöverträdelse{'r' if n > 1 else ''}")
        title = "Körning klar — kall överträdelse"
        priority = "high"
        tags = "warning"

    if recovered:
        title = "Förra körningen (sparad vid uppstart)"

    return {
        "title": title,
        "body": ", ".join(parts),
        "priority": priority,
        "tags": tags,
    }


class Notifier:
    def __init__(self, cfg) -> None:
        self.url = f"{cfg.ntfy_url.rstrip('/')}/{cfg.topic}"
        self.timeout = cfg.timeout_s

    def send(self, msg: dict) -> bool:
        """One ntfy message. False on any failure — offline is the normal
        case in the car, never an error; the queue retries later."""
        try:
            resp = requests.post(
                self.url,
                data=msg["body"].encode("utf-8"),
                headers={
                    "Title": msg["title"],
                    "Priority": msg["priority"],
                    "Tags": msg["tags"],
                },
                timeout=self.timeout,
            )
            if resp.status_code >= 300:
                log.info("Push rejected: HTTP %d", resp.status_code)
                return False
            log.info("Pushed to phone: %s", msg["title"])
            return True
        except requests.RequestException as exc:
            log.debug("No push sent (offline): %s", exc)
            return False


class PushQueue:
    """Persistent at-least-once queue for phone notifications.

    Network appears and disappears (phone hotspot during drives, power cut
    at ignition off), so pushes are queued to disk and drained whenever the
    sync worker runs. A trip's notification survives any number of power
    cuts and goes out the first time the Pi is online, in order.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items))
        tmp.replace(self.path)

    def add(self, msg: dict) -> None:
        with self._lock:
            items = self._load()
            items.append(msg)
            self._save(items)

    def drain(self, send: Callable[[dict], bool]) -> int:
        """Send queued messages in order; stop at the first failure (if one
        fails, the rest will too). Returns how many were sent."""
        with self._lock:
            items = self._load()
            sent = 0
            while items and send(items[0]):
                items.pop(0)
                sent += 1
            if sent:
                self._save(items)
        return sent
