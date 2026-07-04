"""Status LED.

Drives a common-cathode RGB LED via gpiozero. On machines without GPIO (dev
on a Mac, tests, CI) it degrades to logging the mode transitions, so the rest
of the daemon never needs to know whether real hardware is present.

Modes:
    WAITING        blue slow blink   (no ECU / engine off)
    COLD           solid red
    COLD_VIOLATION fast red blink
    WARM           solid green
"""

from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)

RED = (1, 0, 0)
GREEN = (0, 1, 0)
BLUE = (0, 0, 1)


class LedMode(Enum):
    OFF = "off"
    WAITING = "waiting"
    COLD = "cold"
    COLD_VIOLATION = "cold_violation"
    WARM = "warm"


class StatusLed:
    def __init__(self, cfg) -> None:
        self._mode: LedMode | None = None
        self._led = None
        if not cfg.enabled:
            return
        try:
            from gpiozero import RGBLED

            self._led = RGBLED(red=cfg.red_pin, green=cfg.green_pin, blue=cfg.blue_pin)
        except Exception as exc:  # no GPIO on this machine
            log.warning("GPIO unavailable (%s) — LED runs in log-only mode", exc)

    def set_mode(self, mode: LedMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        log.info("LED -> %s", mode.value)
        if self._led is None:
            return
        if mode is LedMode.OFF:
            self._led.off()
        elif mode is LedMode.WAITING:
            self._led.blink(on_time=0.2, off_time=1.8, on_color=BLUE, background=True)
        elif mode is LedMode.COLD:
            self._led.color = RED
        elif mode is LedMode.COLD_VIOLATION:
            self._led.blink(on_time=0.15, off_time=0.15, on_color=RED, background=True)
        elif mode is LedMode.WARM:
            self._led.color = GREEN

    def close(self) -> None:
        if self._led is not None:
            self._led.off()
            self._led.close()
            self._led = None
