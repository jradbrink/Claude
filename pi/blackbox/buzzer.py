"""In-car audio feedback via an active 5 V buzzer module on a GPIO pin.

Sound replaces the LED as the primary in-car indicator: it works without
looking anywhere, needs no hole in the interior trim, and only speaks when
something changes — no constant "state lamp".

Signals (patterns, since an active buzzer is on/off only):

    violation while cold   rapid urgent beeping for as long as it lasts
    coolant warm (V1.5)    one short beep (optional, default off)
    fully warm             two short beeps — "engine is ready"

Like the LED, this degrades to log-only on machines without GPIO.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class StatusBuzzer:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._buzzer = None
        self._violation_on = False
        if not cfg.enabled:
            return
        try:
            from gpiozero import Buzzer

            self._buzzer = Buzzer(cfg.pin)
        except Exception as exc:  # no GPIO on this machine
            log.warning("GPIO unavailable (%s) — buzzer runs in log-only mode", exc)

    def set_violation(self, active: bool) -> None:
        """Urgent beeping while a cold violation is in progress."""
        if not self.cfg.enabled or active == self._violation_on:
            return
        self._violation_on = active
        log.info("Buzzer: violation %s", "START" if active else "STOP")
        if self._buzzer is None:
            return
        if active:
            self._buzzer.beep(on_time=0.1, off_time=0.1, background=True)
        else:
            self._buzzer.off()

    def chime_coolant_warm(self) -> None:
        if not self.cfg.enabled or not self.cfg.chime_on_coolant_warm:
            return
        log.info("Buzzer: coolant warm chime")
        if self._buzzer is not None and not self._violation_on:
            self._buzzer.beep(on_time=0.15, off_time=0.1, n=1, background=True)

    def chime_warm(self) -> None:
        """Two short beeps: fully warmed up, rev away."""
        if not self.cfg.enabled or not self.cfg.chime_on_warm:
            return
        log.info("Buzzer: fully warm chime")
        if self._buzzer is not None and not self._violation_on:
            self._buzzer.beep(on_time=0.15, off_time=0.1, n=2, background=True)

    def close(self) -> None:
        if self._buzzer is not None:
            self._buzzer.off()
            self._buzzer.close()
            self._buzzer = None
