"""GuardrailPort: per-turn prompt-injection and abuse screening, via the agent-guardrail-gateway.

Every inbound turn is screened AFTER redaction and BEFORE any retrieval or generation call (see
``domain/guardrails.py``, which owns that ordering). The remote family is a thin S2S client to the
shared agent-guardrail-gateway Agent Guardrail Gateway; marketing-compliance-gate's platform family
is the reference for the transport.

**Unavailable is a verdict, not an exception the caller may ignore.** An adapter that cannot
reach the gateway raises, and ``TurnGuard`` converts the raise into
``ScreenOutcome.UNAVAILABLE``, which fails closed per mode. What no adapter may do is return
CLEAN when it did not screen: that is the one failure that would make the whole control
decorative.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import ScreenResult


@runtime_checkable
class GuardrailPort(Protocol):
    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        """Screen one already-redacted turn. Raise rather than returning a fabricated CLEAN."""
        ...
