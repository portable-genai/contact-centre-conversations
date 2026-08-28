"""Every condition that MUST reach a human, named once, in one table.

Before this module the conditions were spread across five places: ``handoff.decide_trigger``,
``guardrails.degradation_for``, ``action_engine.decide``, ``DisclosureReport`` and then a
hand-written boolean expression in each mode service. Five homes and two expressions is how the
two expressions came to disagree, and they did: self-service omitted ``degradation.review``, so
a guardrail-blocked turn in the CUSTOMER-FACING mode escalated to nobody while the identical
event in the lower-risk agent-assist mode routed correctly. Nothing was wrong with either
service in isolation. The defect only existed in the gap between them.

So "escalation was required" is a function of stated facts rather than an expression a reader
has to reconstruct. Both services call :func:`reasons_for` with what they observed, and a mode
that wants a different answer has to change the table rather than its own boolean.

Two things this module does NOT do. It does not decide whether a contact leaves self-service for
a person: that is ``handoff.decide_trigger``, which answers a different question (this contact
needs a human NOW) from this one (this outcome must be reviewed by a human, whether or not the
contact is still live). And it does not route: routing is ``ports/review_router.py`` and rule
R8, and the flag is not the escalation. Naming a reason here obliges the caller to route it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .guardrails import Degradation
from .models import ActionOutcome, DisclosureReport

__all__ = [
    "ACTION_REVIEW",
    "MISSED_DISCLOSURE",
    "SCREEN_DEGRADED",
    "EscalationReason",
    "reasons_for",
]

#: A disclosure window closed unsatisfied. Consequential by definition: the obligation was to
#: say something by a deadline, and the deadline passed.
MISSED_DISCLOSURE = "missed_disclosure"

#: The guardrail blocked the turn, or could not be reached, in a mode where that must be seen.
#: This is the reason the two services disagreed about, so it is the reason with the longest
#: name in the table and the one the regression test in
#: ``tests/unit/test_escalation_table.py`` watches most closely.
SCREEN_DEGRADED = "screen_degraded"

#: The action engine prepared something a person has to decide: a consequential action, a
#: parameter naming a record the caller was not shown to own, or a gate verdict short of ALLOW.
#: The engine's own detail travels with it rather than being re-derived here.
ACTION_REVIEW = "action_review"


@dataclass(frozen=True, slots=True)
class EscalationReason:
    """One reason this outcome must reach a human, and what to tell them."""

    code: str
    detail: str


def reasons_for(
    *,
    degradation: Degradation,
    disclosures: DisclosureReport,
    action: ActionOutcome | None = None,
) -> tuple[EscalationReason, ...]:
    """Every reason this turn's outcome must be reviewed, in a stable order.

    The order is severity-independent and fixed, so two runs of the same contact produce the
    same list and a reviewer reading two cases side by side is comparing like with like.

    Returns an EMPTY tuple when nothing must escalate, which is the ordinary case and is not a
    failure. The caller turns a non-empty result into a routed review; a caller that set a flag
    and stopped there would have escalated to nobody, which is the defect above one level up.
    """
    reasons: list[EscalationReason] = []
    if disclosures.requires_human_review:
        missed = ", ".join(status.disclosure_id for status in disclosures.missed)
        reasons.append(
            EscalationReason(
                code=MISSED_DISCLOSURE,
                detail=f"disclosure window closed unsatisfied: {missed}",
            )
        )
    if degradation.review:
        reasons.append(EscalationReason(code=SCREEN_DEGRADED, detail=degradation.detail))
    if action is not None and action.requires_human_review:
        reasons.append(EscalationReason(code=ACTION_REVIEW, detail=action.detail))
    return tuple(reasons)
