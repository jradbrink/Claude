"""Trip recording: aggregates samples into one summary row per drive.

Distance is a trapezoidal integral of OBD vehicle speed (PID 010D). The
integration step is capped so that a gap in samples (adapter hiccup) cannot
manufacture phantom kilometres; the report always labels distance as
estimated.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from .state import ColdStartMonitor, ColdViolation


@dataclass
class TripSummary:
    id: str
    vehicle_id: str
    device_id: str
    started_at: datetime
    ended_at: datetime
    duration_s: int
    distance_km_est: float
    warmed_up: bool
    warmup_s: int | None          # time until fully warm per criterion
    coolant_warmup_s: int | None  # time until coolant threshold
    oil_warmup_s: int | None      # time until oil threshold (None without oil data)
    warm_criterion: str           # "coolant" | "oil_and_coolant"
    max_rpm: int
    max_coolant_c: float | None
    max_oil_temp_c: float | None
    cold_violation_count: int
    violations: list[ColdViolation] = field(default_factory=list)

    def as_row(self) -> dict:
        """Trip row in the exact shape of the Supabase `trips` table."""
        return {
            "id": self.id,
            "vehicle_id": self.vehicle_id,
            "device_id": self.device_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat(),
            "duration_s": self.duration_s,
            "distance_km_est": round(self.distance_km_est, 2),
            "warmed_up": self.warmed_up,
            "warmup_s": self.warmup_s,
            "coolant_warmup_s": self.coolant_warmup_s,
            "oil_warmup_s": self.oil_warmup_s,
            "warm_criterion": self.warm_criterion,
            "max_rpm": self.max_rpm,
            "max_coolant_c": (
                round(self.max_coolant_c, 1) if self.max_coolant_c is not None else None
            ),
            "max_oil_temp_c": (
                round(self.max_oil_temp_c, 1) if self.max_oil_temp_c is not None else None
            ),
            "cold_violation_count": self.cold_violation_count,
            "source": "pi",
        }

    def event_rows(self) -> list[dict]:
        return [
            {
                "id": v.id,
                "trip_id": self.id,
                "started_at": v.started_at.isoformat() if v.started_at else None,
                "ended_at": v.ended_at.isoformat() if v.ended_at else None,
                "duration_s": round(v.duration_s, 1) if v.duration_s is not None else None,
                "max_rpm": v.max_rpm,
                "coolant_c_at_start": (
                    round(v.coolant_c_at_start, 1)
                    if v.coolant_c_at_start is not None
                    else None
                ),
                "oil_c_at_start": (
                    round(v.oil_c_at_start, 1) if v.oil_c_at_start is not None else None
                ),
            }
            for v in self.violations
        ]


class TripRecorder:
    def __init__(
        self,
        vehicle_id: str,
        device_id: str,
        monitor: ColdStartMonitor,
        started_at: datetime,
        max_integration_dt_s: float = 5.0,
    ) -> None:
        self.id = str(uuid.uuid4())
        self.vehicle_id = vehicle_id
        self.device_id = device_id
        self.monitor = monitor
        self.started_at = started_at
        self.max_integration_dt_s = max_integration_dt_s

        self.distance_km = 0.0
        self.max_rpm = 0
        self.max_coolant_c: float | None = None
        self.max_oil_temp_c: float | None = None
        self._prev_ts: datetime | None = None
        self._prev_speed_kph: float | None = None
        self.last_sample_at = started_at

    def add_sample(
        self,
        now: datetime,
        rpm: float | None,
        coolant_c: float | None,
        speed_kph: float | None,
        oil_c: float | None = None,
    ) -> None:
        self.last_sample_at = now
        self.monitor.update(now, coolant_c, rpm, oil_c)

        if rpm is not None:
            self.max_rpm = max(self.max_rpm, int(rpm))
        if coolant_c is not None:
            self.max_coolant_c = (
                coolant_c
                if self.max_coolant_c is None
                else max(self.max_coolant_c, coolant_c)
            )
        if oil_c is not None:
            self.max_oil_temp_c = (
                oil_c
                if self.max_oil_temp_c is None
                else max(self.max_oil_temp_c, oil_c)
            )

        if speed_kph is not None:
            if self._prev_ts is not None and self._prev_speed_kph is not None:
                dt = (now - self._prev_ts).total_seconds()
                dt = min(dt, self.max_integration_dt_s)
                if dt > 0:
                    avg_kph = (speed_kph + self._prev_speed_kph) / 2.0
                    self.distance_km += avg_kph * dt / 3600.0
            self._prev_ts = now
            self._prev_speed_kph = speed_kph

    def snapshot(self) -> dict:
        """State needed to recover this trip after an abrupt power cut.

        With ignition-switched power (the recommended install) every trip
        ends with a power cut, so this is the *primary* trip-end path: the
        journal written from this dict is turned into a finished trip by
        recover_summary() on the next boot.
        """
        violations = list(self.monitor.completed)
        if self.monitor.open_violation is not None:
            violations = violations + [self.monitor.open_violation]
        return {
            "id": self.id,
            "vehicle_id": self.vehicle_id,
            "device_id": self.device_id,
            "started_at": self.started_at.isoformat(),
            "last_sample_at": self.last_sample_at.isoformat(),
            "distance_km": self.distance_km,
            "max_rpm": self.max_rpm,
            "max_coolant_c": self.max_coolant_c,
            "max_oil_temp_c": self.max_oil_temp_c,
            "warm_criterion": self.monitor.criterion,
            "warmed_at": (
                self.monitor.warmed_at.isoformat() if self.monitor.warmed_at else None
            ),
            "coolant_warmed_at": (
                self.monitor.coolant_warmed_at.isoformat()
                if self.monitor.coolant_warmed_at
                else None
            ),
            "oil_warmed_at": (
                self.monitor.oil_warmed_at.isoformat()
                if self.monitor.oil_warmed_at
                else None
            ),
            "violations": [
                {
                    "id": v.id,
                    "started_at": v.started_at.isoformat() if v.started_at else None,
                    "ended_at": v.ended_at.isoformat() if v.ended_at else None,
                    "max_rpm": v.max_rpm,
                    "coolant_c_at_start": v.coolant_c_at_start,
                    "oil_c_at_start": v.oil_c_at_start,
                }
                for v in violations
            ],
        }

    def finalize(self, ended_at: datetime) -> TripSummary:
        self.monitor.finish(ended_at)

        def since_start(ts: datetime | None) -> int | None:
            return int((ts - self.started_at).total_seconds()) if ts else None

        return TripSummary(
            id=self.id,
            vehicle_id=self.vehicle_id,
            device_id=self.device_id,
            started_at=self.started_at,
            ended_at=ended_at,
            duration_s=int((ended_at - self.started_at).total_seconds()),
            distance_km_est=self.distance_km,
            warmed_up=self.monitor.warmed_at is not None,
            warmup_s=since_start(self.monitor.warmed_at),
            coolant_warmup_s=since_start(self.monitor.coolant_warmed_at),
            oil_warmup_s=since_start(self.monitor.oil_warmed_at),
            warm_criterion=self.monitor.criterion,
            max_rpm=self.max_rpm,
            max_coolant_c=self.max_coolant_c,
            max_oil_temp_c=self.max_oil_temp_c,
            cold_violation_count=len(self.monitor.completed),
            violations=list(self.monitor.completed),
        )


def recover_summary(data: dict) -> TripSummary:
    """Rebuild a finished TripSummary from a journal snapshot written before
    a power cut. The trip is closed at the last sample we saw."""
    started_at = datetime.fromisoformat(data["started_at"])
    ended_at = datetime.fromisoformat(data["last_sample_at"])
    violations = [
        ColdViolation(
            id=v["id"],
            started_at=datetime.fromisoformat(v["started_at"]) if v["started_at"] else None,
            ended_at=datetime.fromisoformat(v["ended_at"]) if v["ended_at"] else ended_at,
            max_rpm=v["max_rpm"],
            coolant_c_at_start=v["coolant_c_at_start"],
            oil_c_at_start=v.get("oil_c_at_start"),
        )
        for v in data.get("violations", [])
    ]

    def parse_opt(key: str) -> datetime | None:
        return datetime.fromisoformat(data[key]) if data.get(key) else None

    def since_start(ts: datetime | None) -> int | None:
        return int((ts - started_at).total_seconds()) if ts else None

    warmed_at = parse_opt("warmed_at")
    return TripSummary(
        id=data["id"],
        vehicle_id=data["vehicle_id"],
        device_id=data["device_id"],
        started_at=started_at,
        ended_at=ended_at,
        duration_s=int((ended_at - started_at).total_seconds()),
        distance_km_est=data["distance_km"],
        warmed_up=warmed_at is not None,
        warmup_s=since_start(warmed_at),
        coolant_warmup_s=since_start(parse_opt("coolant_warmed_at")),
        oil_warmup_s=since_start(parse_opt("oil_warmed_at")),
        warm_criterion=data.get("warm_criterion", "coolant"),
        max_rpm=data["max_rpm"],
        max_coolant_c=data["max_coolant_c"],
        max_oil_temp_c=data.get("max_oil_temp_c"),
        cold_violation_count=len(violations),
        violations=violations,
    )
