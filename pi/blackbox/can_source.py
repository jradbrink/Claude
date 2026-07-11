"""Oil temperature from the car's internal CAN bus (V1.5).

The 996.2 never exposes oil temp over generic OBD-II, but the DME broadcasts
it on the powertrain CAN (DME <-> instrument cluster). Community-documented
decode for the 996.2:

    CAN ID 0x4E0, start bit 40 (byte 5), 8 bits, unsigned, intel byte order,
    physical = raw * 0.75 - 48   (range -48 .. 142.5 C)

Hardware: an MCP2515 SPI CAN HAT tapped onto the CAN pair at the instrument
cluster, with the interface brought up in *listen-only* mode (no ACKs, no
error frames — electrically invisible to the car). See README for wiring and
`ip link` setup, and verify the decoded value against a Durametric readout
before trusting it.

The listener runs on a background thread and only ever *publishes* the latest
reading; if the bus is silent (ignition off), frames stop and the reading
goes stale. Everything degrades gracefully: python-can missing, interface
down, or no frames simply means oil_temp_c is None and the daemon behaves
like V1 (coolant-only).
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)


def decode_oil_temp(data: bytes, byte_index: int, factor: float, offset: float) -> float | None:
    if len(data) <= byte_index:
        return None
    return data[byte_index] * factor + offset


class OilTempCanListener:
    def __init__(self, cfg) -> None:
        self.channel = cfg.channel
        self.interface = cfg.interface
        self.bitrate = cfg.bitrate
        self.can_id = cfg.can_id
        self.byte_index = cfg.byte_index
        self.factor = cfg.factor
        self.offset = cfg.offset
        self.stale_after_s = cfg.stale_after_s
        self._value: float | None = None
        self._value_at = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="can-oil", daemon=True)
        self._thread.start()

    @property
    def oil_temp_c(self) -> float | None:
        """Latest decoded oil temp, or None if never seen or gone stale."""
        with self._lock:
            if self._value is None:
                return None
            if time.monotonic() - self._value_at > self.stale_after_s:
                return None
            return self._value

    def _run(self) -> None:
        try:
            import can
        except ImportError:
            log.warning("python-can not installed — oil temp disabled")
            return

        while not self._stop.is_set():
            bus = None
            try:
                kwargs = {
                    "channel": self.channel,
                    "interface": self.interface,
                    "can_filters": [{"can_id": self.can_id, "can_mask": 0x7FF}],
                }
                if self.bitrate:  # slcan and friends; socketcan sets it at link level
                    kwargs["bitrate"] = self.bitrate
                bus = can.Bus(**kwargs)
                log.info("CAN listener up on %s (id 0x%X)", self.channel, self.can_id)
                while not self._stop.is_set():
                    msg = bus.recv(timeout=1.0)
                    if msg is None:
                        continue
                    value = decode_oil_temp(
                        bytes(msg.data), self.byte_index, self.factor, self.offset
                    )
                    if value is not None:
                        with self._lock:
                            self._value = value
                            self._value_at = time.monotonic()
            except Exception as exc:
                log.warning("CAN bus error on %s: %s — retrying in 10 s", self.channel, exc)
                self._stop.wait(10.0)
            finally:
                if bus is not None:
                    try:
                        bus.shutdown()
                    except Exception:
                        pass

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
