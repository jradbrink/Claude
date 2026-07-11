"""Cold-start state machine.

One ColdStartMonitor instance lives for exactly one trip. Warm-up is tracked
with independent latches for coolant and (optionally) oil temperature:

    COLD ──coolant ≥ threshold──► COOLANT_WARM ──oil ≥ threshold──► WARM
      │                                                              ▲
      └────────── coolant-only criterion (V1 behaviour) ─────────────┘

On the M96 the oil lags the coolant by 10+ minutes, so with oil data present
(V1.5 CAN listener) the engine is not "warm" until BOTH are up. All latches
are permanent within the trip — a thermostat dip at speed never un-warms the
engine. If oil is required but no oil reading ever arrives (broken CAN wire,
HAT missing), the monitor degrades to the coolant-only criterion so the LED
can still go green; the trip row records which criterion actually applied.

Violations are recorded as *episodes*, not samples: revving past the limit
for three seconds at 2 Hz polling is one event, not six rows. An episode
opens when RPM exceeds the limit before the engine is fully WARM, tracks its
own max RPM, and closes when RPM has stayed below (limit - hysteresis) for a
debounce period, or when the engine reaches WARM, or when the trip ends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EngineState(Enum):
    COLD = "cold"
    COOLANT_WARM = "coolant_warm"  # coolant up, oil still warming
    WARM = "warm"


@dataclass
class ColdViolation:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    max_rpm: int = 0
    coolant_c_at_start: float | None = None
    oil_c_at_start: float | None = None

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None or self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()


class ColdStartMonitor:
    def __init__(
        self,
        warm_coolant_c: float = 80.0,
        cold_rpm_limit: int = 3000,
        rpm_hysteresis: int = 300,
        violation_end_delay_s: float = 2.0,
        warm_oil_c: float = 80.0,
        require_oil: bool = False,
    ) -> None:
        self.warm_coolant_c = warm_coolant_c
        self.warm_oil_c = warm_oil_c
        self.require_oil = require_oil
        self.cold_rpm_limit = cold_rpm_limit
        self.rpm_release = cold_rpm_limit - rpm_hysteresis
        self.violation_end_delay_s = violation_end_delay_s

        self.state = EngineState.COLD
        self.coolant_warmed_at: datetime | None = None
        self.oil_warmed_at: datetime | None = None
        self.warmed_at: datetime | None = None  # fully warm per criterion
        self.oil_seen = False
        self.completed: list[ColdViolation] = []
        self._active: ColdViolation | None = None
        self._below_since: datetime | None = None
        self._last_oil_c: float | None = None

    @property
    def violation_active(self) -> bool:
        return self._active is not None

    @property
    def open_violation(self) -> ColdViolation | None:
        return self._active

    @property
    def criterion(self) -> str:
        """The warm criterion actually in effect for this trip."""
        if self.require_oil and self.oil_seen:
            return "oil_and_coolant"
        return "coolant"

    def update(
        self,
        now: datetime,
        coolant_c: float | None,
        rpm: float | None,
        oil_c: float | None = None,
    ) -> None:
        """Feed one sample. Missing readings (None) are simply skipped."""
        if oil_c is not None:
            self.oil_seen = True
            self._last_oil_c = oil_c
            if self.oil_warmed_at is None and oil_c >= self.warm_oil_c:
                self.oil_warmed_at = now
        if (
            coolant_c is not None
            and self.coolant_warmed_at is None
            and coolant_c >= self.warm_coolant_c
        ):
            self.coolant_warmed_at = now

        if self.warmed_at is None:  # WARM is a permanent latch
            coolant_ok = self.coolant_warmed_at is not None
            oil_required = self.require_oil and self.oil_seen
            oil_ok = self.oil_warmed_at is not None
            if coolant_ok and (oil_ok or not oil_required):
                self.state = EngineState.WARM
                self.warmed_at = now
                self._close_active(now)
            elif coolant_ok:
                self.state = EngineState.COOLANT_WARM

        if rpm is None or self.state is EngineState.WARM:
            return

        if self._active is None:
            if rpm > self.cold_rpm_limit:
                self._active = ColdViolation(
                    started_at=now,
                    max_rpm=int(rpm),
                    coolant_c_at_start=coolant_c,
                    oil_c_at_start=oil_c if oil_c is not None else self._last_oil_c,
                )
                self._below_since = None
        else:
            self._active.max_rpm = max(self._active.max_rpm, int(rpm))
            if rpm < self.rpm_release:
                if self._below_since is None:
                    self._below_since = now
                elif (now - self._below_since).total_seconds() >= self.violation_end_delay_s:
                    self._close_active(now)
            else:
                self._below_since = None

    def finish(self, now: datetime) -> None:
        """Trip is over — close any episode still open."""
        self._close_active(now)

    def _close_active(self, now: datetime) -> None:
        if self._active is not None:
            self._active.ended_at = now
            self.completed.append(self._active)
            self._active = None
            self._below_since = None
