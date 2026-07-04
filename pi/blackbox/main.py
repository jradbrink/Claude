"""Black Box daemon.

Lifecycle per drive:

    WAITING (blue blink) ── ECU answers & RPM >= engine_on_rpm ──► trip starts
        │                                                              │
        │   opportunistic Supabase sync                                │ poll @ 2 Hz:
        │   (rate-limited, engine off)                                 │ state machine, LED,
        │                                                              │ journal every 15 s
        ◄── engine off / ECU silent / power cut ── trip finalized ─────┘
             (power cut: journal recovered on next boot)

Run:  python -m blackbox.main --config /etc/blackbox.toml
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .buzzer import StatusBuzzer
from .can_source import OilTempCanListener
from .config import AppConfig, load_config
from .led import LedMode, StatusLed
from .notify import Notifier, PushQueue, format_trip_message
from .obd_source import ObdSource, create_source
from .state import ColdStartMonitor, EngineState
from .storage import Storage
from .sync import SupabaseSync
from .trip import TripRecorder, recover_summary

log = logging.getLogger("blackbox")

JOURNAL_WRITE_EVERY_S = 15.0
RECONNECT_DELAY_S = 5.0


class Daemon:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.led = StatusLed(cfg.led)
        self.buzzer = StatusBuzzer(cfg.buzzer)
        self.notifier = Notifier(cfg.notify) if cfg.notify.enabled else None
        self.storage = Storage(cfg.storage.db_path)
        self.sync = (
            SupabaseSync(cfg.supabase, self.storage, cfg.device_id)
            if cfg.supabase.enabled
            else None
        )
        if self.sync is None:
            log.warning("Supabase not configured — running local-only")
        self.source: ObdSource = create_source(cfg.obd)
        self.can_listener: OilTempCanListener | None = None
        if cfg.can.enabled:
            self.can_listener = OilTempCanListener(cfg.can)
            self.can_listener.start()
        self.require_oil = cfg.thresholds.warm_criterion == "oil_and_coolant" or (
            cfg.thresholds.warm_criterion == "auto"
            and (cfg.can.enabled or cfg.obd.mock)
        )
        state_dir = Path(cfg.storage.db_path).parent
        self.journal_path = state_dir / "active_trip.json"
        self.push_queue = PushQueue(state_dir / "push_queue.json")
        self.stop_requested = False
        # -inf so the first opportunity syncs immediately (monotonic starts
        # near zero on a fresh boot, which would otherwise delay it).
        self._last_sync_attempt = float("-inf")
        self._last_journal_write = 0.0
        self._prev_engine_state = EngineState.COLD
        self._sync_thread: threading.Thread | None = None

    # -- crash recovery -----------------------------------------------------

    def recover_journal(self) -> None:
        """A journal file at boot means the last trip ended with a power cut
        (the normal case with ignition-switched power). Close and save it."""
        if not self.journal_path.exists():
            return
        try:
            data = json.loads(self.journal_path.read_text())
            summary = recover_summary(data)
        except (ValueError, KeyError):
            log.exception("Corrupt trip journal — discarding")
            self.journal_path.unlink(missing_ok=True)
            return
        if summary.duration_s >= self.cfg.trip.min_trip_duration_s:
            self.storage.save_trip(summary)
            log.info(
                "Recovered trip %s from journal (%ds, %.1f km)",
                summary.id, summary.duration_s, summary.distance_km_est,
            )
            self.queue_push(summary, recovered=True)
        else:
            log.info("Recovered journal trip shorter than %ss — discarded",
                     self.cfg.trip.min_trip_duration_s)
        self.journal_path.unlink(missing_ok=True)

    # -- sync & push (background thread; network comes and goes) ------------

    def queue_push(self, summary, recovered: bool = False) -> None:
        if self.notifier is not None:
            self.push_queue.add(format_trip_message(summary, recovered))

    def _sync_worker(self) -> None:
        if self.sync is not None:
            self.sync.sync_once()
        if self.notifier is not None:
            self.push_queue.drain(self.notifier.send)

    def maybe_sync(self, force: bool = False) -> None:
        """Kick the background sync/push worker. Never blocks the poll loop:
        with phone-hotspot connectivity the network is only there DURING the
        drive, so this also runs while a trip is active."""
        if self.sync is None and self.notifier is None:
            return
        now = time.monotonic()
        if not force and now - self._last_sync_attempt < self.cfg.supabase.sync_interval_s:
            return
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return
        self._last_sync_attempt = now
        self._sync_thread = threading.Thread(
            target=self._sync_worker, name="sync", daemon=True
        )
        self._sync_thread.start()

    # -- trip loop ----------------------------------------------------------

    def indicate_for_trip(self, monitor: ColdStartMonitor) -> None:
        # Buzzer: urgent beeps track the violation, chimes fire on the
        # warm-up transitions (edge-detected).
        self.buzzer.set_violation(monitor.violation_active)
        if monitor.state is not self._prev_engine_state:
            if monitor.state is EngineState.COOLANT_WARM:
                self.buzzer.chime_coolant_warm()
            elif monitor.state is EngineState.WARM:
                self.buzzer.chime_warm()
            self._prev_engine_state = monitor.state

        # LED: optional, disabled by default since the buzzer took over.
        if monitor.violation_active:
            self.led.set_mode(LedMode.COLD_VIOLATION)
        elif monitor.state is EngineState.WARM:
            self.led.set_mode(LedMode.WARM)
        elif monitor.state is EngineState.COOLANT_WARM:
            self.led.set_mode(LedMode.WARMING_OIL)
        else:
            self.led.set_mode(LedMode.COLD)

    def write_journal(self, trip: TripRecorder) -> None:
        now = time.monotonic()
        if now - self._last_journal_write < JOURNAL_WRITE_EVERY_S:
            return
        self._last_journal_write = now
        tmp = self.journal_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(trip.snapshot()))
        tmp.replace(self.journal_path)

    def end_trip(self, trip: TripRecorder, ended_at: datetime) -> None:
        summary = trip.finalize(ended_at)
        if summary.duration_s >= self.cfg.trip.min_trip_duration_s:
            self.storage.save_trip(summary)
            log.info(
                "Trip saved: %ds, %.1f km est, max %d rpm, warmed_up=%s, violations=%d",
                summary.duration_s, summary.distance_km_est, summary.max_rpm,
                summary.warmed_up, summary.cold_violation_count,
            )
            self.queue_push(summary)
            self.maybe_sync(force=True)
        else:
            log.info("Trip shorter than %ss — discarded", self.cfg.trip.min_trip_duration_s)
        self.buzzer.set_violation(False)
        self.journal_path.unlink(missing_ok=True)

    def run_connected(self) -> None:
        """Poll until the ECU goes silent or the engine stays off."""
        tcfg = self.cfg.trip
        trip: TripRecorder | None = None
        disconnect_since: float | None = None
        engine_off_since: datetime | None = None

        while not self.stop_requested:
            sample = self.source.read()
            now_mono = time.monotonic()

            if sample is None:
                if disconnect_since is None:
                    disconnect_since = now_mono
                if now_mono - disconnect_since >= tcfg.disconnect_end_s:
                    if trip is not None:
                        self.end_trip(trip, trip.last_sample_at)
                    return
                time.sleep(self.cfg.obd.poll_interval_s)
                continue
            disconnect_since = None

            if trip is None:
                if sample.rpm is not None and sample.rpm >= tcfg.engine_on_rpm:
                    monitor = ColdStartMonitor(
                        warm_coolant_c=self.cfg.thresholds.warm_coolant_c,
                        cold_rpm_limit=self.cfg.thresholds.cold_rpm_limit,
                        rpm_hysteresis=self.cfg.thresholds.rpm_hysteresis,
                        violation_end_delay_s=self.cfg.thresholds.violation_end_delay_s,
                        warm_oil_c=self.cfg.thresholds.warm_oil_c,
                        require_oil=self.require_oil,
                    )
                    trip = TripRecorder(
                        vehicle_id=self.cfg.vehicle_id,
                        device_id=self.cfg.device_id,
                        monitor=monitor,
                        started_at=sample.ts,
                        max_integration_dt_s=tcfg.max_integration_dt_s,
                    )
                    engine_off_since = None
                    self._prev_engine_state = EngineState.COLD
                    log.info("Trip started (%s)", trip.id)
                else:
                    self.led.set_mode(LedMode.WAITING)
                    self.maybe_sync()

            if trip is not None:
                oil_c = sample.oil_c
                if oil_c is None and self.can_listener is not None:
                    oil_c = self.can_listener.oil_temp_c
                trip.add_sample(
                    sample.ts, sample.rpm, sample.coolant_c, sample.speed_kph, oil_c
                )
                self.indicate_for_trip(trip.monitor)
                self.write_journal(trip)
                self.maybe_sync()  # hotspot case: network exists mid-drive

                if sample.rpm is None or sample.rpm < tcfg.engine_off_rpm:
                    if engine_off_since is None:
                        engine_off_since = sample.ts
                    elif (sample.ts - engine_off_since).total_seconds() >= tcfg.engine_off_end_s:
                        self.end_trip(trip, engine_off_since)
                        trip = None
                        engine_off_since = None
                else:
                    engine_off_since = None

            time.sleep(self.cfg.obd.poll_interval_s)

        if trip is not None:  # graceful shutdown (SIGTERM) mid-trip
            self.end_trip(trip, trip.last_sample_at)

    def run(self) -> None:
        self.recover_journal()
        try:
            while not self.stop_requested:
                self.led.set_mode(LedMode.WAITING)
                if not self.source.connect():
                    self.maybe_sync()
                    # Sleep in short slices so SIGTERM stays responsive.
                    deadline = time.monotonic() + RECONNECT_DELAY_S
                    while not self.stop_requested and time.monotonic() < deadline:
                        time.sleep(0.2)
                    continue
                self.run_connected()
                self.source.close()
        finally:
            self.source.close()
            if self.can_listener is not None:
                self.can_listener.close()
            self.led.set_mode(LedMode.OFF)
            self.led.close()
            self.buzzer.close()
            self.storage.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Black Box V1 daemon")
    parser.add_argument("--config", default="/etc/blackbox.toml")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_config(args.config)
    daemon = Daemon(cfg)

    def request_stop(signum, frame):
        log.info("Signal %s — shutting down", signum)
        daemon.stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    daemon.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
