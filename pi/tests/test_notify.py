from datetime import datetime, timedelta, timezone

from blackbox.notify import format_trip_message
from blackbox.trip import TripSummary

T0 = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)


def make_summary(**overrides) -> TripSummary:
    base = dict(
        id="t1", vehicle_id="v1", device_id="d1",
        started_at=T0, ended_at=T0 + timedelta(minutes=42),
        duration_s=2520, distance_km_est=38.2,
        warmed_up=True, warmup_s=1380, coolant_warmup_s=840, oil_warmup_s=1380,
        warm_criterion="oil_and_coolant",
        max_rpm=4200, max_coolant_c=91.0, max_oil_temp_c=88.0,
        cold_violation_count=0, violations=[],
    )
    base.update(overrides)
    return TripSummary(**base)


def test_clean_trip_message():
    msg = format_trip_message(make_summary())
    assert msg["title"] == "Körning klar ✔"
    assert msg["priority"] == "default"
    assert "42 min" in msg["body"]
    assert "38.2 km" in msg["body"]
    assert "uppvärmd efter 23 min (kylvätska 14, olja 23)" in msg["body"]
    assert "inga kallstartsöverträdelser" in msg["body"]


def test_violation_trip_is_high_priority():
    msg = format_trip_message(make_summary(cold_violation_count=2))
    assert msg["priority"] == "high"
    assert "2 kallstartsöverträdelser" in msg["body"]


def test_never_warm_trip():
    msg = format_trip_message(
        make_summary(warmed_up=False, warmup_s=None,
                     coolant_warmup_s=None, oil_warmup_s=None)
    )
    assert "nådde aldrig arbetstemperatur" in msg["body"]


def test_recovered_trip_title():
    msg = format_trip_message(make_summary(), recovered=True)
    assert "uppstart" in msg["title"]
