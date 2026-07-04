"""Configuration loading.

Everything lives in a TOML file (see pi/config.example.toml). Supabase
credentials may instead come from the environment (SUPABASE_URL /
SUPABASE_SERVICE_KEY), which is how the systemd unit provides them so the
secret never sits in the config file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Thresholds:
    warm_coolant_c: float = 80.0
    cold_rpm_limit: int = 3000
    rpm_hysteresis: int = 300
    violation_end_delay_s: float = 2.0


@dataclass(frozen=True)
class ObdConfig:
    port: str = ""  # empty = let python-OBD auto-detect
    poll_interval_s: float = 0.5
    coolant_every_n_polls: int = 4  # coolant moves slowly; poll it less often
    mock: bool = False


@dataclass(frozen=True)
class TripConfig:
    engine_on_rpm: int = 300
    engine_off_rpm: int = 100
    engine_off_end_s: float = 60.0
    disconnect_end_s: float = 45.0
    min_trip_duration_s: float = 60.0
    max_integration_dt_s: float = 5.0


@dataclass(frozen=True)
class LedConfig:
    enabled: bool = True
    red_pin: int = 17
    green_pin: int = 27
    blue_pin: int = 22


@dataclass(frozen=True)
class StorageConfig:
    db_path: str = "/var/lib/blackbox/blackbox.db"


@dataclass(frozen=True)
class SupabaseConfig:
    url: str = ""
    service_key: str = ""
    sync_interval_s: float = 300.0
    timeout_s: float = 10.0

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.service_key)


@dataclass(frozen=True)
class AppConfig:
    vehicle_id: str
    device_id: str
    thresholds: Thresholds = field(default_factory=Thresholds)
    obd: ObdConfig = field(default_factory=ObdConfig)
    trip: TripConfig = field(default_factory=TripConfig)
    led: LedConfig = field(default_factory=LedConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    supabase: SupabaseConfig = field(default_factory=SupabaseConfig)


def _section(data: dict, name: str, cls):
    return cls(**data.get(name, {}))


def load_config(path: str | Path) -> AppConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)

    vehicle = data.get("vehicle", {})
    vehicle_id = vehicle.get("vehicle_id", "")
    device_id = vehicle.get("device_id", "")
    if not vehicle_id or not device_id:
        raise ValueError("config: [vehicle] vehicle_id and device_id are required")

    supa = data.get("supabase", {})
    supa["url"] = os.environ.get("SUPABASE_URL", supa.get("url", ""))
    supa["service_key"] = os.environ.get(
        "SUPABASE_SERVICE_KEY", supa.get("service_key", "")
    )

    return AppConfig(
        vehicle_id=vehicle_id,
        device_id=device_id,
        thresholds=_section(data, "thresholds", Thresholds),
        obd=_section(data, "obd", ObdConfig),
        trip=_section(data, "trip", TripConfig),
        led=_section(data, "led", LedConfig),
        storage=_section(data, "storage", StorageConfig),
        supabase=SupabaseConfig(**supa),
    )
