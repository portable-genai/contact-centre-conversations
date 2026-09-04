"""Local GuardrailPort: a deterministic offline screen over an explicit injection cue set.

It is a stand-in for the agent-guardrail-gateway, and it is a REAL screen rather than a
pass-through: the cue set below is the corpus the injection tests are built from, so a turn carrying
a known injection pattern is BLOCKED offline exactly as it would be by the gateway. That matters
because the property under test is "an injection never reaches the generation port", and a local
adapter that waved everything through would make that test vacuous while keeping it green.

The cues are lowercase substrings matched against the already-redacted text. Crude on purpose:
this is the offline family, and a clever local screen would tempt somebody to ship it.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ScreenOutcome, ScreenResult

#: Injection and jailbreak cues. Every entry is a phrase that only appears when somebody is
#: addressing the SYSTEM rather than the agent, which is what makes it screenable at all.
INJECTION_CUES: tuple[tuple[str, str], ...] = (
    ("ignore previous instructions", "instruction-override"),
    ("ignore all previous", "instruction-override"),
    ("disregard your instructions", "instruction-override"),
    ("system prompt", "prompt-exfiltration"),
    ("reveal your prompt", "prompt-exfiltration"),
    ("you are now", "role-override"),
    ("act as an unrestricted", "role-override"),
    ("developer mode", "role-override"),
    ("print the contents of", "data-exfiltration"),
)


class LocalCueGuardrailAdapter:
    """Screen already-redacted text against a fixed cue set. SDK-free and deterministic."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        lowered = text.lower()
        categories = tuple(sorted({category for cue, category in INJECTION_CUES if cue in lowered}))
        if categories:
            return ScreenResult(
                outcome=ScreenOutcome.BLOCKED,
                turn_index=turn_index,
                detail="the turn addresses the system rather than the agent",
                categories=categories,
            )
        return ScreenResult(
            outcome=ScreenOutcome.CLEAN, turn_index=turn_index, detail="no injection cue matched"
        )
