from datetime import datetime, timedelta, timezone

from blackbox.state import ColdStartMonitor
from blackbox.trip import TripRecorder, recover_summary

T0 = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_trip(**kwargs) -> TripRecorder:
    monitor = ColdStartMonitor()
    return TripRecorder(
        vehicle_id="veh-1", device_id="dev-1", monitor=monitor, started_at=at(0), **kwargs
    )


def test_distance_integration_constant_speed():
    trip = make_trip()
    # 60 km/h for 60 seconds = 1 km
    for s in range(0, 61):
        trip.add_sample(at(s), 2000, 50.0, 60.0)
    summary = trip.finalize(at(61))
    assert abs(summary.distance_km_est - 1.0) < 0.01


def test_sample_gap_does_not_create_phantom_distance():
    trip = make_trip(max_integration_dt_s=5.0)
    trip.add_sample(at(0), 2000, 50.0, 100.0)
    trip.add_sample(at(120), 2000, 50.0, 100.0)  # 2 min dropout at 100 km/h
    summary = trip.finalize(at(121))
    # Capped at 5 s of integration, not 120 s (which would be 3.3 km).
    assert summary.distance_km_est <= 100.0 * 5 / 3600 + 0.001


def test_summary_aggregates():
    trip = make_trip()
    trip.add_sample(at(0), 1200, 20.0, 0.0)
    trip.add_sample(at(10), 4200, 25.0, 30.0)  # cold violation
    trip.add_sample(at(20), 2000, 25.0, 40.0)
    trip.add_sample(at(30), 2500, 82.0, 50.0)  # warmed up
    summary = trip.finalize(at(600))

    assert summary.max_rpm == 4200
    assert summary.max_coolant_c == 82.0
    assert summary.warmed_up
    assert summary.warmup_s == 30
    assert summary.duration_s == 600
    assert summary.cold_violation_count == 1
    row = summary.as_row()
    assert row["vehicle_id"] == "veh-1"
    assert row["cold_violation_count"] == 1
    assert len(summary.event_rows()) == 1


def test_never_warmed_trip():
    trip = make_trip()
    trip.add_sample(at(0), 1000, 15.0, 0.0)
    trip.add_sample(at(60), 1500, 40.0, 20.0)
    summary = trip.finalize(at(120))
    assert not summary.warmed_up
    assert summary.warmup_s is None


def test_journal_snapshot_roundtrip():
    trip = make_trip()
    trip.add_sample(at(0), 1200, 20.0, 0.0)
    trip.add_sample(at(5), 4000, 22.0, 20.0)  # open violation at power cut
    trip.add_sample(at(10), 4100, 24.0, 30.0)

    snapshot = trip.snapshot()  # what the journal held when power died
    summary = recover_summary(snapshot)

    assert summary.id == trip.id
    assert summary.ended_at == at(10)
    assert summary.duration_s == 10
    assert summary.max_rpm == 4100
    assert not summary.warmed_up
    assert summary.cold_violation_count == 1
    # The open episode is closed at the last sample time.
    assert summary.violations[0].ended_at == at(10)
    assert summary.violations[0].max_rpm == 4100
    # Rows must be shaped for the cloud tables.
    assert summary.as_row()["duration_s"] == 10
    assert summary.event_rows()[0]["trip_id"] == trip.id
