"""OBD data sources.

Elm327Source talks to a real adapter through python-OBD. MockSource simulates
a cold start followed by a short drive, so the whole daemon (state machine,
LED, storage, sync) can be developed and demoed on a desk with no car.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)


@dataclass
class Sample:
    ts: datetime
    rpm: float | None
    coolant_c: float | None
    speed_kph: float | None


class ObdSource:
    def connect(self) -> bool:
        raise NotImplementedError

    def read(self) -> Sample | None:
        """One sample, or None if the adapter/ECU stopped responding."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class Elm327Source(ObdSource):
    def __init__(self, port: str = "", coolant_every_n_polls: int = 4) -> None:
        self.port = port or None
        self.coolant_every_n_polls = max(1, coolant_every_n_polls)
        self._conn = None
        self._poll_count = 0
        self._last_coolant: float | None = None

    def connect(self) -> bool:
        import obd

        try:
            self._conn = obd.OBD(self.port, fast=True, timeout=1.0)
        except Exception:
            log.exception("OBD connect failed")
            self._conn = None
            return False
        ok = self._conn.status() == obd.OBDStatus.CAR_CONNECTED
        if not ok:
            log.warning("OBD adapter found but no ECU (status=%s)", self._conn.status())
            self._conn.close()
            self._conn = None
        else:
            log.info("Connected to ECU on %s", self._conn.port_name())
        return ok

    def read(self) -> Sample | None:
        import obd

        if self._conn is None:
            return None
        now = datetime.now(timezone.utc)

        rpm = self._query(obd.commands.RPM)
        speed = self._query(obd.commands.SPEED)
        if self._poll_count % self.coolant_every_n_polls == 0:
            coolant = self._query(obd.commands.COOLANT_TEMP)
            if coolant is not None:
                self._last_coolant = coolant
        self._poll_count += 1

        if rpm is None and speed is None:
            # ECU stops answering when ignition goes off; treat as disconnect.
            return None
        return Sample(ts=now, rpm=rpm, coolant_c=self._last_coolant, speed_kph=speed)

    def _query(self, cmd) -> float | None:
        try:
            resp = self._conn.query(cmd)
        except Exception:
            log.exception("OBD query %s failed", cmd.name)
            return None
        if resp is None or resp.is_null():
            return None
        return float(resp.value.magnitude)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


class MockSource(ObdSource):
    """Simulated cold start + drive, ~4 minutes: idle warm-up, an early
    over-rev (triggers a cold violation), coolant reaching 80 °C, then a
    cruise and engine off."""

    def __init__(self, time_scale: float = 10.0) -> None:
        self.time_scale = time_scale  # 10x = the 4-minute drive takes ~24 s
        self._t0: float | None = None

    def connect(self) -> bool:
        self._t0 = time.monotonic()
        log.info("MockSource connected (time_scale=%sx)", self.time_scale)
        return True

    def read(self) -> Sample | None:
        t = (time.monotonic() - self._t0) * self.time_scale
        now = datetime.now(timezone.utc)
        if t > 240:  # engine off
            return None
        coolant = min(92.0, 15.0 + t * 0.45)  # ~80 °C after ~145 s
        if t < 20:
            rpm, speed = 1100.0, 0.0  # cold idle
        elif t < 26:
            rpm, speed = 3800.0, 10.0  # someone revs it cold -> violation
        elif t < 150:
            rpm, speed = 2000.0, 40.0  # gentle driving while warming
        else:
            rpm, speed = 2800.0, 90.0  # warmed up, cruising
        return Sample(ts=now, rpm=rpm, coolant_c=coolant, speed_kph=speed)

    def close(self) -> None:
        self._t0 = None


def create_source(cfg) -> ObdSource:
    if cfg.mock:
        return MockSource()
    return Elm327Source(cfg.port, cfg.coolant_every_n_polls)
