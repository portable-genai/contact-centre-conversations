"""DTMF digit collection: RFC 4733 events in, one dialled string out.

A caller keying digits produces a burst of telephone-event packets per digit (the same event
repeated, then end-flagged). This collector deduplicates to one digit per key press and closes
a dialled string on the terminator key or on inter-digit silence, handing the session one
string it can treat exactly like an utterance the recognizer produced.

Digits are frequently PII (an account number, a card PAN), so the collector's output goes
through the SAME redact-then-screen turn pipeline as speech, and the session redacts before
forwarding any digit string to a live engine. Pure state machine, caller supplies the clock.
"""

from __future__ import annotations

from .rtp import TelephoneEvent


class DigitCollector:
    """Accumulate key presses into dialled strings. The caller drives time explicitly."""

    def __init__(self, *, terminator: str = "#", inter_digit_timeout_ms: int = 3000) -> None:
        self._terminator = terminator
        self._timeout_ms = inter_digit_timeout_ms
        self._digits: list[str] = []
        self._last_digit_at_ms: int | None = None
        self._in_event = False

    def on_event(self, event: TelephoneEvent, *, now_ms: int) -> str | None:
        """Feed one telephone-event packet; return a completed dialled string, or None.

        The FIRST non-end packet of a press registers the digit (so a lost end packet cannot
        lose the digit); the repeats and the end packet only close the press. An end packet
        arriving while no press is open is an RFC 4733 end-packet RETRANSMISSION (the final
        packet is deliberately sent three times) and must be ignored, not read as a fresh
        press: reading it as one triples every digit a real trunk delivers.
        """
        completed: str | None = None
        if not self._in_event and not event.end:
            self._in_event = True
            if event.digit == self._terminator:
                completed = self._flush()
            else:
                self._digits.append(event.digit)
                self._last_digit_at_ms = now_ms
        if event.end:
            self._in_event = False
        return completed

    def on_tick(self, *, now_ms: int) -> str | None:
        """Close the dialled string when the caller has stopped keying. Caller calls this on
        its own cadence; nothing here reads a clock."""
        if (
            self._digits
            and self._last_digit_at_ms is not None
            and now_ms - self._last_digit_at_ms >= self._timeout_ms
        ):
            return self._flush()
        return None

    def _flush(self) -> str | None:
        if not self._digits:
            return None
        dialled = "".join(self._digits)
        self._digits.clear()
        self._last_digit_at_ms = None
        return dialled
