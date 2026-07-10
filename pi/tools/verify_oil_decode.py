#!/usr/bin/env python3
"""Verify the 996.2 oil-temp CAN decode using the car itself as reference —
no Durametric needed.

Run this on the Pi during one cold-start warm-up (engine on, ~20 min):

    python3 verify_oil_decode.py --port /dev/ttyUSB0 --out /tmp/oilverify

It simultaneously:
  1. polls OBD-II coolant temp + RPM via the OBDLink (the reference signal),
  2. listens on can0 for the target frame (default 0x4E0) and decodes the
     candidate oil temp (default byte 5, x0.75 - 48),
and then evaluates the physics every M96 warm-up must obey:

  START   cold engine: oil ~= coolant ~= ambient (within a tolerance)
  SHAPE   oil rises monotonically while the engine runs
  LAG     oil reaches 80 C later than coolant does
  RANGE   oil plateaus in a sane range (75-115 C)
  CORR    oil correlates strongly with coolant over the warm-up (Pearson)

All checks green -> the decode is almost certainly correct. Any red -> do
NOT trust the decode; re-run with --dump and reverse engineer properly:
--dump also records ALL raw CAN frames plus the OBD reference into
time-sorted CSVs, the exact input a correlation-based reverse-engineering
workflow (e.g. the CSS Electronics Claude Code skill) needs.
"""

from __future__ import annotations

import argparse
import csv
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------- analysis --

def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def nearest(series: list[tuple[float, float]], t: float, max_dt: float = 3.0) -> float | None:
    """Value in (ts, value) series closest to time t, within max_dt seconds."""
    best, best_dt = None, max_dt
    for ts, v in series:
        dt = abs(ts - t)
        if dt <= best_dt:
            best, best_dt = v, dt
    return best


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def first_crossing(series: list[tuple[float, float]], threshold: float) -> float | None:
    for ts, v in series:
        if v >= threshold:
            return ts
    return None


def evaluate(
    coolant: list[tuple[float, float]],
    oil: list[tuple[float, float]],
    warm_c: float = 80.0,
    start_tolerance_c: float = 15.0,
) -> list[Check]:
    """The five physics checks. Both series are (t_seconds, temp_c), t
    relative to the same clock."""
    checks: list[Check] = []
    if len(coolant) < 10 or len(oil) < 10:
        return [Check("DATA", False,
                      f"too few samples (coolant={len(coolant)}, oil={len(oil)})")]

    c0, o0 = coolant[0][1], oil[0][1]
    checks.append(Check(
        "START", abs(c0 - o0) <= start_tolerance_c,
        f"first samples: coolant {c0:.0f} C, oil-candidate {o0:.0f} C "
        f"(tolerance {start_tolerance_c:.0f} C)",
    ))

    values = [v for _, v in oil]
    rises = sum(1 for a, b in zip(values, values[1:]) if b >= a - 0.8)
    frac = rises / (len(values) - 1)
    checks.append(Check(
        "SHAPE", frac >= 0.9,
        f"{frac * 100:.0f}% of steps non-decreasing (need >= 90%)",
    ))

    t_cool = first_crossing(coolant, warm_c)
    t_oil = first_crossing(oil, warm_c)
    if t_cool is None:
        checks.append(Check("LAG", False, f"coolant never reached {warm_c:.0f} C — drive longer"))
    elif t_oil is None:
        last = values[-1]
        # Oil still climbing when the run ended is consistent with real oil.
        checks.append(Check(
            "LAG", last > o0 + 20,
            f"oil-candidate never reached {warm_c:.0f} C (ended at {last:.0f} C) — "
            "ok if still climbing, otherwise drive longer",
        ))
    else:
        lag = t_oil - t_cool
        checks.append(Check(
            "LAG", lag > 60,
            f"oil-candidate hit {warm_c:.0f} C {lag / 60:.1f} min after coolant (expect several minutes)",
        ))

    peak = max(values)
    checks.append(Check(
        "RANGE", 75.0 <= peak <= 115.0 or t_oil is None,
        f"peak oil-candidate {peak:.0f} C (sane plateau 75-115 C)",
    ))

    pairs = [(v, nearest(oil, ts)) for ts, v in coolant]
    xs = [c for c, o in pairs if o is not None]
    ys = [o for c, o in pairs if o is not None]
    r = pearson(xs, ys)
    checks.append(Check(
        "CORR", r is not None and r >= 0.9,
        f"Pearson r = {r:.3f} over {len(xs)} paired samples (need >= 0.9)" if r is not None
        else "not enough overlapping samples",
    ))
    return checks


# ----------------------------------------------------------------- capture --

class CanCapture(threading.Thread):
    def __init__(self, channel: str, can_id: int, byte_index: int,
                 factor: float, offset: float, dump_all: bool) -> None:
        super().__init__(daemon=True)
        self.channel = channel
        self.can_id = can_id
        self.byte_index = byte_index
        self.factor = factor
        self.offset = offset
        self.dump_all = dump_all
        self.decoded: list[tuple[float, float]] = []  # (t, oil_c)
        self.raw: list[tuple[float, int, str]] = []   # (t, id, hex) if dump_all
        self.stop = threading.Event()
        self.error: str | None = None

    def run(self) -> None:
        try:
            import can
        except ImportError:
            self.error = "python-can not installed"
            return
        try:
            filters = None if self.dump_all else [{"can_id": self.can_id, "can_mask": 0x7FF}]
            bus = can.Bus(channel=self.channel, interface="socketcan", can_filters=filters)
        except Exception as exc:
            self.error = f"CAN bus open failed: {exc}"
            return
        try:
            while not self.stop.is_set():
                msg = bus.recv(timeout=1.0)
                if msg is None:
                    continue
                t = time.time()
                if self.dump_all:
                    self.raw.append((t, msg.arbitration_id, msg.data.hex()))
                if msg.arbitration_id == self.can_id and len(msg.data) > self.byte_index:
                    self.decoded.append(
                        (t, msg.data[self.byte_index] * self.factor + self.offset)
                    )
        finally:
            bus.shutdown()


def poll_obd(port: str, duration_s: float, interval_s: float,
             coolant_out: list[tuple[float, float]],
             rpm_out: list[tuple[float, float]]) -> None:
    import obd

    conn = obd.OBD(port or None, fast=True, timeout=1.0)
    if conn.status() != obd.OBDStatus.CAR_CONNECTED:
        raise SystemExit(f"No ECU via OBD (status={conn.status()}) — is the engine on?")
    end = time.time() + duration_s
    while time.time() < end:
        t = time.time()
        for cmd, out in ((obd.commands.COOLANT_TEMP, coolant_out),
                         (obd.commands.RPM, rpm_out)):
            resp = conn.query(cmd)
            if resp is not None and not resp.is_null():
                out.append((t, float(resp.value.magnitude)))
        remaining = max(0.0, end - time.time())
        print(f"\r  coolant={coolant_out[-1][1]:.0f}C samples={len(coolant_out)} "
              f"left={remaining / 60:.1f}min   ", end="", flush=True)
        time.sleep(interval_s)
    conn.close()
    print()


# -------------------------------------------------------------------- main --

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="", help="OBD serial port ('' = auto)")
    ap.add_argument("--channel", default="can0")
    ap.add_argument("--can-id", type=lambda v: int(v, 0), default=0x4E0)
    ap.add_argument("--byte-index", type=int, default=5)
    ap.add_argument("--factor", type=float, default=0.75)
    ap.add_argument("--offset", type=float, default=-48.0)
    ap.add_argument("--minutes", type=float, default=20.0, help="capture length")
    ap.add_argument("--interval", type=float, default=2.0, help="OBD poll interval (s)")
    ap.add_argument("--out", default="./oilverify", help="output directory")
    ap.add_argument("--dump", action="store_true",
                    help="also record ALL raw CAN frames for reverse engineering")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Capturing {args.minutes:.0f} min: OBD via '{args.port or 'auto'}', "
          f"CAN {args.channel} id 0x{args.can_id:X} byte {args.byte_index} "
          f"(x{args.factor} {args.offset:+g}){' + full dump' if args.dump else ''}")
    print("Start this with a COLD engine, then drive/idle until coolant is warm.\n")

    cap = CanCapture(args.channel, args.can_id, args.byte_index,
                     args.factor, args.offset, args.dump)
    cap.start()
    coolant: list[tuple[float, float]] = []
    rpm: list[tuple[float, float]] = []
    try:
        poll_obd(args.port, args.minutes * 60, args.interval, coolant, rpm)
    finally:
        cap.stop.set()
        cap.join(timeout=3)

    if cap.error:
        raise SystemExit(f"CAN capture failed: {cap.error}")

    # Save everything (times as relative seconds from capture start).
    t0 = min([coolant[0][0]] if coolant else [time.time()]
             + ([cap.decoded[0][0]] if cap.decoded else []))
    with open(out / "reference_obd.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "signal", "value"])
        for ts, v in coolant:
            w.writerow([f"{ts - t0:.2f}", "coolant_c", f"{v:.1f}"])
        for ts, v in rpm:
            w.writerow([f"{ts - t0:.2f}", "rpm", f"{v:.0f}"])
    with open(out / "oil_candidate.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "oil_c_candidate"])
        for ts, v in cap.decoded:
            w.writerow([f"{ts - t0:.2f}", f"{v:.1f}"])
    if args.dump:
        with open(out / "raw_can_trace.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_s", "can_id_hex", "data_hex"])
            for ts, cid, data in cap.raw:
                w.writerow([f"{ts - t0:.3f}", f"{cid:X}", data])
        print(f"Raw trace: {len(cap.raw)} frames -> {out / 'raw_can_trace.csv'}")

    # Verdict
    oil_rel = [(ts - t0, v) for ts, v in cap.decoded]
    cool_rel = [(ts - t0, v) for ts, v in coolant]
    checks = evaluate(cool_rel, oil_rel)
    print("\n=== Decode verification ===")
    for c in checks:
        print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name:6s} {c.detail}")
    if all(c.passed for c in checks):
        print("\nAll checks passed — the decode behaves exactly like M96 oil temp."
              "\nSafe to set [can] enabled = true.")
    else:
        print("\nOne or more checks FAILED — do not trust the decode yet."
              "\nRe-run with --dump and reverse engineer against the raw trace"
              "\n(reference_obd.csv + raw_can_trace.csv are ready for that workflow).")


if __name__ == "__main__":
    main()
