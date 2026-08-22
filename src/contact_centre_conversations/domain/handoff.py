"""Handoff with context carry-over: deterministic triggers, a checked package, a replayable state.

The worst version of a handoff is the common one: the customer is told a person is coming, the
person arrives with nothing, and the customer starts again. So two properties matter more than
the mechanism.

**The triggers are deterministic.** A model never decides that a contact should leave
self-service. Five conditions do, and each is a fact somebody else already established: the gate
denied, the same intent failed repeatedly, the customer asked for a person, a vulnerability cue
was matched by the kit's lexicon, or the guardrail screen was unavailable so the mode had to
degrade and self-service has nowhere safe to degrade TO.

**The package is schema-checked before it leaves.** ``validate_package`` is called by the
producer, not by the consumer, because an incomplete handoff has already cost the customer their
patience by the time the receiving agent notices. The transcript carried is the REDACTED one.

The carry-over is the procedure state ids, and it is replayed through the SAME engine on the
receiving side (``procedure_engine.replay_carry_over``) rather than assigned. The replay test
asserts the resumed progress equals the progress that was handed over, which is the only way to
know the two sides agree about where the contact is.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from speech_lexicon_kit import ChannelRole, Lexicon, SpeakerTurn, find_matches

from .kernel import Citation
from .models import (
    ActionCall,
    ContactRef,
    HandoffPackage,
    HandoffTrigger,
    PolicyVerdict,
    ProcedureProgress,
    ScreenOutcome,
)

__all__ = [
    "HandoffValidationError",
    "build_package",
    "decide_trigger",
    "validate_package",
]

#: How many turns of failed intent matching count as "repeated". Two is the point at which a
#: customer has already rephrased once and been misunderstood twice.
REPEATED_FAILURE_THRESHOLD = 2


class HandoffValidationError(ValueError):
    """The assembled package is missing something the receiving agent needs."""


def decide_trigger(
    *,
    verdict: PolicyVerdict | None,
    consecutive_failures: int,
    customer_text: str,
    escalation_lexicon: Lexicon | None,
    vulnerability_lexicon: Lexicon | None,
    screen_failure: ScreenOutcome | None = None,
    consequential_action: bool = False,
) -> HandoffTrigger | None:
    """The one trigger that fired, in a fixed precedence, or None to stay in self-service.

    Precedence is fixed rather than "most severe wins" because the trigger is recorded as the
    REASON the customer was transferred, and a customer who asked for a person should see that
    they asked, not a policy code. Safety conditions still come first.
    """
    if screen_failure is ScreenOutcome.BLOCKED:
        return HandoffTrigger.SCREEN_BLOCKED
    if screen_failure is ScreenOutcome.UNAVAILABLE:
        return HandoffTrigger.SCREEN_UNAVAILABLE
    if vulnerability_lexicon is not None and find_matches(customer_text, vulnerability_lexicon):
        return HandoffTrigger.VULNERABILITY
    if escalation_lexicon is not None and find_matches(customer_text, escalation_lexicon):
        return HandoffTrigger.CUSTOMER_REQUEST
    if verdict is not None and verdict.denied:
        return HandoffTrigger.GATE_DENIAL
    if consecutive_failures >= REPEATED_FAILURE_THRESHOLD:
        return HandoffTrigger.REPEATED_FAILED_INTENT
    if consequential_action:
        return HandoffTrigger.CONSEQUENTIAL_ACTION
    return None


def build_package(
    contact: ContactRef,
    *,
    trigger: HandoffTrigger,
    redacted_turns: Sequence[SpeakerTurn],
    progress: ProcedureProgress | None,
    verdicts: Sequence[PolicyVerdict],
    created_at: datetime,
    pending_action: ActionCall | None = None,
    citations: Sequence[Citation] = (),
) -> HandoffPackage:
    """Assemble the package, then validate it. Assembly and validation are one act.

    Returning an unvalidated package would let a caller forget the second half, and the whole
    point of the check is that it happens on the producing side.
    """
    package = HandoffPackage(
        contact_id=contact.contact_id,
        tenant=contact.tenant,
        market=contact.market,
        trigger=trigger,
        created_at=created_at,
        summary=_summary(trigger, progress, verdicts),
        turns=tuple(redacted_turns),
        procedure_state_id=progress.state_id if progress else "",
        carry_over_state_ids=progress.carry_over if progress else (),
        gate_verdicts=tuple(verdicts),
        pending_action=pending_action,
        citations=tuple(citations),
    )
    validate_package(package)
    return package


def _summary(
    trigger: HandoffTrigger,
    progress: ProcedureProgress | None,
    verdicts: Sequence[PolicyVerdict],
) -> str:
    """One sentence the receiving agent reads first. Facts only, assembled from the artifacts."""
    where = f"at procedure state {progress.state_id!r}" if progress else "with no procedure state"
    denied = [reason.code for verdict in verdicts for reason in verdict.reasons if verdict.denied]
    because = f" after {', '.join(sorted(set(denied)))}" if denied else ""
    return f"Transferred on {trigger.value} {where}{because}."


def validate_package(package: HandoffPackage) -> None:
    """Refuse a package that would arrive useless. Producer-side, always.

    Each rule below is something a receiving agent cannot work without, and each has a failure
    mode that is invisible until a customer is already on the line.
    """
    if not package.contact_id.strip():
        raise HandoffValidationError("handoff package has no contact id")
    if not package.tenant.strip():
        raise HandoffValidationError(
            "handoff package has no tenant: an unpartitioned package cannot be authorised on "
            "arrival, and a queue that cannot authorise is a queue that shows everything"
        )
    if not package.turns:
        raise HandoffValidationError(
            "handoff package carries no transcript: the customer would have to start again, "
            "which is the failure the carry-over exists to prevent"
        )
    if not package.summary.strip():
        raise HandoffValidationError("handoff package has no summary")
    if package.trigger is HandoffTrigger.GATE_DENIAL and not package.gate_verdicts:
        raise HandoffValidationError(
            "a gate-denial handoff must carry the verdicts that denied it, or the receiving "
            "agent cannot tell the customer why a machine refused"
        )
    if package.pending_action is not None and not package.pending_action.action_id:
        raise HandoffValidationError("handoff package carries a pending action with no id")
    if any(turn.role is ChannelRole.UNKNOWN for turn in package.turns):
        raise HandoffValidationError(
            "handoff package carries a turn with no speaker role: a transcript whose speakers "
            "are unattributed cannot be read safely by somebody who was not on the contact"
        )
