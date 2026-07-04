from datetime import datetime, timedelta, timezone

from blackbox.state import ColdStartMonitor, EngineState

T0 = datetime(2026, 7, 1, 8, 0, 0, tzinfo=timezone.utc)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def make_monitor() -> ColdStartMonitor:
    return ColdStartMonitor(
        warm_coolant_c=80.0,
        cold_rpm_limit=3000,
        rpm_hysteresis=300,
        violation_end_delay_s=2.0,
    )


def test_warm_latch_is_permanent():
    m = make_monitor()
    m.update(at(0), 20.0, 1000)
    assert m.state is EngineState.COLD
    m.update(at(100), 80.0, 1000)
    assert m.state is EngineState.WARM
    assert m.warmed_at == at(100)
    # Thermostat dip below the threshold must not un-warm the engine.
    m.update(at(200), 76.0, 2000)
    assert m.state is EngineState.WARM


def test_sustained_rev_is_one_episode():
    m = make_monitor()
    for s in range(0, 6):  # 3500 rpm for 6 seconds, 1 Hz samples
        m.update(at(s), 30.0, 3500)
    assert m.violation_active
    # Drop below release threshold (2700) and hold for the debounce delay.
    m.update(at(7), 30.0, 2000)
    m.update(at(9.5), 30.0, 2000)
    assert not m.violation_active
    assert len(m.completed) == 1
    v = m.completed[0]
    assert v.max_rpm == 3500
    assert v.started_at == at(0)
    assert v.coolant_c_at_start == 30.0


def test_brief_dip_does_not_split_episode():
    m = make_monitor()
    m.update(at(0), 25.0, 3600)
    m.update(at(1), 25.0, 2500)  # below release, but only briefly
    m.update(at(1.5), 25.0, 3800)  # back up before the 2 s debounce
    m.update(at(3), 25.0, 2000)
    m.update(at(6), 25.0, 2000)
    assert len(m.completed) == 1
    assert m.completed[0].max_rpm == 3800


def test_no_violation_when_warm():
    m = make_monitor()
    m.update(at(0), 85.0, 1000)
    assert m.state is EngineState.WARM
    m.update(at(1), 85.0, 6500)
    assert not m.violation_active
    assert m.completed == []


def test_warming_up_closes_open_episode():
    m = make_monitor()
    m.update(at(0), 78.0, 4000)
    assert m.violation_active
    m.update(at(2), 81.0, 4000)
    assert m.state is EngineState.WARM
    assert not m.violation_active
    assert len(m.completed) == 1
    assert m.completed[0].ended_at == at(2)


def test_trip_end_closes_open_episode():
    m = make_monitor()
    m.update(at(0), 30.0, 3500)
    m.finish(at(4))
    assert len(m.completed) == 1
    assert m.completed[0].duration_s == 4.0


def test_missing_readings_are_skipped():
    m = make_monitor()
    m.update(at(0), None, None)
    m.update(at(1), None, 3500)  # coolant unknown yet, still cold -> violation
    assert m.violation_active
    m.update(at(2), None, None)  # dropout does not close the episode
    assert m.violation_active
