from datetime import datetime, timedelta, timezone

from blackbox.can_source import decode_oil_temp
from blackbox.state import ColdStartMonitor, EngineState
from blackbox.trip import TripRecorder, recover_summary

T0 = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_monitor(require_oil: bool = True) -> ColdStartMonitor:
    return ColdStartMonitor(
        warm_coolant_c=80.0, warm_oil_c=80.0, require_oil=require_oil,
        cold_rpm_limit=3000, rpm_hysteresis=300, violation_end_delay_s=2.0,
    )


def test_decode_oil_temp_996_layout():
    # 996.2: byte 5, physical = raw * 0.75 - 48
    data = bytes([0, 0, 0, 0, 0, 170, 0, 0])
    assert decode_oil_temp(data, 5, 0.75, -48.0) == 79.5
    assert decode_oil_temp(bytes([0] * 8), 5, 0.75, -48.0) == -48.0
    assert decode_oil_temp(b"\x00\x01", 5, 0.75, -48.0) is None  # short frame


def test_not_warm_until_oil_is_up():
    m = make_monitor()
    m.update(at(0), 20.0, 1000, oil_c=15.0)
    assert m.state is EngineState.COLD
    # Coolant reaches threshold long before oil (M96 behaviour).
    m.update(at(300), 82.0, 2000, oil_c=55.0)
    assert m.state is EngineState.COOLANT_WARM
    assert m.warmed_at is None
    m.update(at(900), 90.0, 2000, oil_c=81.0)
    assert m.state is EngineState.WARM
    assert m.warmed_at == at(900)
    assert m.oil_warmed_at == at(900)
    assert m.coolant_warmed_at == at(300)
    assert m.criterion == "oil_and_coolant"


def test_violation_still_logged_when_coolant_warm_but_oil_cold():
    m = make_monitor()
    m.update(at(0), 82.0, 2000, oil_c=50.0)
    assert m.state is EngineState.COOLANT_WARM
    m.update(at(1), 82.0, 4500, oil_c=50.0)
    assert m.violation_active
    assert m.open_violation.oil_c_at_start == 50.0


def test_missing_oil_data_degrades_to_coolant_only():
    # Broken CAN wire: oil required but never seen -> V1 behaviour, LED can
    # still go green, and the trip records the criterion actually applied.
    m = make_monitor(require_oil=True)
    m.update(at(0), 20.0, 1000, oil_c=None)
    m.update(at(300), 82.0, 2000, oil_c=None)
    assert m.state is EngineState.WARM
    assert m.criterion == "coolant"


def test_oil_criterion_not_required_when_disabled():
    m = make_monitor(require_oil=False)
    # Oil data present and cold, but criterion is coolant-only.
    m.update(at(0), 82.0, 2000, oil_c=40.0)
    assert m.state is EngineState.WARM
    assert m.criterion == "coolant"


def test_trip_summary_and_journal_carry_oil_fields():
    trip = TripRecorder(
        vehicle_id="veh-1", device_id="dev-1",
        monitor=make_monitor(), started_at=at(0),
    )
    trip.add_sample(at(0), 1000, 20.0, 0.0, oil_c=15.0)
    trip.add_sample(at(300), 2000, 82.0, 50.0, oil_c=55.0)
    trip.add_sample(at(900), 2000, 90.0, 50.0, oil_c=81.0)

    summary = trip.finalize(at(1200))
    assert summary.warmed_up
    assert summary.coolant_warmup_s == 300
    assert summary.oil_warmup_s == 900
    assert summary.warmup_s == 900  # fully warm when oil came up
    assert summary.max_oil_temp_c == 81.0
    assert summary.warm_criterion == "oil_and_coolant"
    row = summary.as_row()
    assert row["oil_warmup_s"] == 900
    assert row["max_oil_temp_c"] == 81.0
    assert row["warm_criterion"] == "oil_and_coolant"

    # Journal roundtrip (power cut mid-trip, before oil warm)
    trip2 = TripRecorder(
        vehicle_id="veh-1", device_id="dev-1",
        monitor=make_monitor(), started_at=at(0),
    )
    trip2.add_sample(at(0), 1000, 20.0, 0.0, oil_c=15.0)
    trip2.add_sample(at(300), 2000, 82.0, 50.0, oil_c=55.0)
    recovered = recover_summary(trip2.snapshot())
    assert not recovered.warmed_up
    assert recovered.coolant_warmup_s == 300
    assert recovered.oil_warmup_s is None
    assert recovered.max_oil_temp_c == 55.0
    assert recovered.warm_criterion == "oil_and_coolant"
