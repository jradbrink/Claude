"""Cold-start state machine.

One ColdStartMonitor instance lives for exactly one trip. The engine starts
COLD and latches to WARM the first time coolant reaches the threshold; it
never goes back to COLD within the same trip (thermostat dips below the
threshold at speed do not mean the engine is cold again).

Violations are recorded as *episodes*, not samples: revving past the limit
for three seconds at 2 Hz polling is one event, not six rows. An episode
opens when RPM exceeds the limit while COLD, tracks its own max RPM, and
closes when RPM has stayed below (limit - hysteresis) for a debounce period,
or when the engine warms up, or when the trip ends.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EngineState(Enum):
    COLD = "cold"
    WARM = "warm"


@dataclass
class ColdViolation:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime | None = None
    ended_at: datetime | None = None
    max_rpm: int = 0
    coolant_c_at_start: float | None = None

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
    ) -> None:
        self.warm_coolant_c = warm_coolant_c
        self.cold_rpm_limit = cold_rpm_limit
        self.rpm_release = cold_rpm_limit - rpm_hysteresis
        self.violation_end_delay_s = violation_end_delay_s

        self.state = EngineState.COLD
        self.warmed_at: datetime | None = None
        self.completed: list[ColdViolation] = []
        self._active: ColdViolation | None = None
        self._below_since: datetime | None = None

    @property
    def violation_active(self) -> bool:
        return self._active is not None

    @property
    def open_violation(self) -> ColdViolation | None:
        return self._active

    def update(
        self, now: datetime, coolant_c: float | None, rpm: float | None
    ) -> None:
        """Feed one sample. Missing readings (None) are simply skipped."""
        if (
            self.state is EngineState.COLD
            and coolant_c is not None
            and coolant_c >= self.warm_coolant_c
        ):
            self.state = EngineState.WARM
            self.warmed_at = now
            self._close_active(now)

        if rpm is None or self.state is EngineState.WARM:
            return

        if self._active is None:
            if rpm > self.cold_rpm_limit:
                self._active = ColdViolation(
                    started_at=now,
                    max_rpm=int(rpm),
                    coolant_c_at_start=coolant_c,
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
