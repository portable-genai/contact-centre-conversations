"""Per-turn guardrails: redact FIRST, screen SECOND, and fail closed when the screen is gone.

Every inbound turn passes through here before any other part of the service sees it, and the
ORDER is the control rather than a convention:

1. **Redact.** ``pii-kit`` masks personal data while the text is still inside this process. A
   turn that reached a knowledge base or a model unredacted cannot be un-sent, so redaction is
   not a step the pipeline performs on the way out.
2. **Screen.** The redacted text goes to the Hrz1 guardrail for prompt-injection and abuse
   screening. Screening the REDACTED text is deliberate: the screen is an external service, and
   handing it the raw identifiers to look for injections in would leak them to solve a problem
   that has nothing to do with them.
3. **Only then** may a retrieval or generation port be called, and only with the redacted turn.

:class:`TurnGuard` exists so that ordering is a property of a single object rather than a rule
each caller remembers. Neither orchestrator can reach a raw turn: they are handed the guard's
output, which is a redacted :class:`~speech_lexicon_kit.SpeakerTurn` and a verdict.

**Screen unavailable fails closed, differently per mode.** Agent-assist degrades to
deterministic-only: the engines still run (they never saw a model anyway) and the suggestion is
suppressed, because a human agent is present and losing a suggestion costs a suggestion.
Self-service has no such fallback, because there is nobody in the room, so it hands off.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pii_kit import Pattern, redact
from speech_lexicon_kit import RedactionSpan, SpeakerTurn

from .models import ScreenOutcome, ScreenResult, TurnSubmission
from .modes import ContactMode

__all__ = ["GUARD_INFO_TYPE", "ScreenCall", "TurnGuard", "degradation_for"]

#: The info type recorded on the span the redactor produced. The pack masks with its own
#: per-pattern labels; this records THAT masking happened over the whole turn, which is what a
#: reviewer needs when the masked text no longer shows where the boundaries were.
GUARD_INFO_TYPE = "pii-kit"

#: What a screening adapter looks like from inside the domain: redacted text in, verdict out.
#: Typed structurally so the domain never imports the ports package.
ScreenCall = Callable[[str], ScreenResult]


@dataclass(frozen=True, slots=True)
class GuardedTurn:
    """A turn that has been through the whole guard, and nothing else ever sees the raw one."""

    turn: SpeakerTurn
    screen: ScreenResult
    #: True when redaction actually changed the text, so a test can assert it happened at all.
    redacted: bool

    @property
    def safe_for_model(self) -> bool:
        return self.screen.safe_for_model


class TurnGuard:
    """Redact, then screen, in that order, for every inbound turn.

    The screen callable is injected. A guard constructed with a screen that raises is not a
    guard that skips screening: the exception becomes an UNAVAILABLE verdict, which the callers
    treat as fail-closed. Nothing here decides what fail-closed MEANS; that is per mode and
    lives in :func:`degradation_for`.
    """

    def __init__(self, patterns: tuple[Pattern, ...], screen: ScreenCall) -> None:
        self._patterns = patterns
        self._screen = screen

    def guard(self, submission: TurnSubmission) -> GuardedTurn:
        masked = redact(submission.text, self._patterns)
        spans = (
            (
                RedactionSpan(
                    turn_index=submission.index,
                    char_start=0,
                    char_end=max(len(submission.text), 1),
                    info_type=GUARD_INFO_TYPE,
                    replacement=masked,
                ),
            )
            if masked != submission.text
            else ()
        )
        turn = SpeakerTurn(
            index=submission.index,
            speaker_id=submission.speaker_id,
            role=submission.role,
            text=masked,
            start_ms=submission.start_ms,
            end_ms=submission.end_ms,
        )
        try:
            screen = self._screen(masked)
        except Exception as exc:  # noqa: BLE001 - any failure is UNAVAILABLE, never a pass
            screen = ScreenResult(
                outcome=ScreenOutcome.UNAVAILABLE,
                turn_index=submission.index,
                detail=f"guardrail screen unavailable: {type(exc).__name__}: {exc}",
            )
        screen = ScreenResult(
            outcome=screen.outcome,
            turn_index=submission.index,
            detail=screen.detail,
            categories=screen.categories,
            redactions=spans,
        )
        return GuardedTurn(turn=turn, screen=screen, redacted=bool(spans))


@dataclass(frozen=True, slots=True)
class Degradation:
    """What a mode must do about a screen verdict. One table, two modes, no per-caller opinion."""

    #: May a model or knowledge-base port be called at all for this turn?
    allow_model: bool
    #: Must this turn leave the machine and go to a person?
    handoff: bool
    #: Must the result carry ``requires_human_review``?
    review: bool
    detail: str
    #: Which of the two screen failures produced this, so the handoff trigger can say which.
    #: Empty when the screen was clean.
    outcome: ScreenOutcome | None = None


def degradation_for(mode: ContactMode, screen: ScreenResult) -> Degradation:
    """Fail closed, in the way that is safe for THIS mode.

    The asymmetry is the point. Agent-assist has a trained human already on the contact, so
    losing the model is a degraded experience. Self-service has nobody, so the same event is a
    reason to fetch somebody.
    """
    if screen.outcome is ScreenOutcome.CLEAN:
        return Degradation(allow_model=True, handoff=False, review=False, detail="screen clean")
    if screen.outcome is ScreenOutcome.BLOCKED:
        return Degradation(
            allow_model=False,
            handoff=mode is ContactMode.SELF_SERVICE,
            review=True,
            detail="the guardrail blocked this turn, so it reaches no model and no knowledge base",
            outcome=ScreenOutcome.BLOCKED,
        )
    return Degradation(
        allow_model=False,
        handoff=mode is ContactMode.SELF_SERVICE,
        review=mode is ContactMode.SELF_SERVICE,
        detail=(
            "the guardrail screen is unavailable: agent-assist degrades to deterministic-only "
            "and self-service hands off, because an unscreened turn never reaches a model"
        ),
        outcome=ScreenOutcome.UNAVAILABLE,
    )
