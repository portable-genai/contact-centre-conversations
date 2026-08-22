"""The self-service policy gate: fail-closed allowlists, composed worst wins.

This is the module that decides whether a customer-facing machine may act at all. Everything
about it is configuration, and everything about it fails closed:

* **Two allowlists, not one.** The intents the assistant may HANDLE and the actions it may TAKE
  are separate lists, per tenant and per market, because answering a question and moving money
  are not the same permission.
* **An empty allowlist refuses BEFORE anything else.** Not after scoring, not after asking a
  model: first. "Names nobody" means "admits nobody", and evaluating an utterance against an
  empty list and then reporting no match would make an unconfigured tenant look exactly like a
  well-configured tenant whose customer asked something odd.
* **Unmatched, ambiguous or below the floor all DENY.** The confidence is the deterministic one
  from ``intent_engine``; the floor is per intent, in the pack.
* **Verdicts compose worst wins.** Every check contributes a reason with its own outcome and
  the most restrictive one is the verdict, so adding a check can only ever tighten the gate.
  This is the shape proven in ``cio-advisory``'s suitability policy.

A DENY is not an error. It is the gate working, and the caller's response to it is a handoff to
a human, which is why ``handoff.py`` reads the verdict rather than re-deciding.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from .kernel import Citation
from .models import (
    GATE_RANK,
    ActionSpec,
    GateOutcome,
    GateReason,
    IntentMatch,
    PolicyVerdict,
)
from .packs import AllowlistPack

__all__ = ["evaluate", "worst"]

# Reason codes. Stable strings, because they end up in the audit trail and in a metric's
# breakdown, and a reason nobody can group by is a reason nobody acts on.
NO_ALLOWLIST = "no_allowlist"
EMPTY_ALLOWLIST = "empty_allowlist"
TENANT_MISMATCH = "tenant_mismatch"
NO_INTENT_MATCH = "no_intent_match"
BELOW_FLOOR = "below_confidence_floor"
INTENT_ALLOWED = "intent_allowed"
ACTION_NOT_ALLOWLISTED = "action_not_allowlisted"
ACTION_NOT_FOR_INTENT = "action_not_for_intent"
ACTION_UNKNOWN = "action_unknown"
ACTION_CONSEQUENTIAL = "action_consequential"


def worst(reasons: Sequence[GateReason]) -> GateOutcome:
    """The most restrictive outcome among ``reasons``; an empty list is a DENY.

    Empty is DENY rather than ALLOW on purpose. A verdict assembled from no checks has not
    established anything, and the one thing it must not do is read as permission.
    """
    if not reasons:
        return GateOutcome.DENY
    return max((reason.outcome for reason in reasons), key=lambda o: GATE_RANK[o])


def _verdict(
    reasons: Sequence[GateReason],
    *,
    as_of: datetime,
    tenant: str,
    market: str,
    intent: IntentMatch | None = None,
    floor: float = 0.0,
    action_id: str = "",
    citations: Sequence[Citation] = (),
) -> PolicyVerdict:
    return PolicyVerdict(
        outcome=worst(reasons),
        as_of=as_of,
        tenant=tenant,
        market=market,
        intent_id=intent.intent_id if intent else "",
        action_id=action_id,
        confidence=intent.confidence if intent else 0.0,
        confidence_floor=floor,
        reasons=tuple(reasons),
        citations=tuple(citations),
    )


def evaluate(
    pack: AllowlistPack | None,
    *,
    tenant: str,
    market: str,
    intent: IntentMatch | None,
    as_of: datetime,
    requested_action: str = "",
    action_spec: ActionSpec | None = None,
) -> PolicyVerdict:
    """Compose the gate verdict for one customer turn.

    ``intent`` is the deterministic best match from ``intent_engine``; passing it in rather than
    computing it here keeps this function a pure policy composition that a test can drive with
    any confidence value, including the ones a real pack makes hard to produce.
    """
    if pack is None:
        return _verdict(
            [
                GateReason(
                    code=NO_ALLOWLIST,
                    outcome=GateOutcome.DENY,
                    detail=(
                        f"no allowlist is configured for tenant {tenant!r} in market "
                        f"{market!r}: an unconfigured tenant is not an unrestricted one"
                    ),
                )
            ],
            as_of=as_of,
            tenant=tenant,
            market=market,
        )

    # FIRST, before scoring and before any port is touched. See the module docstring.
    if not pack.intents or not pack.allowed_actions:
        return _verdict(
            [
                GateReason(
                    code=EMPTY_ALLOWLIST,
                    outcome=GateOutcome.DENY,
                    detail=(
                        "the allowlist names no intent or no action, so it admits nobody. "
                        "An empty allowlist is the fail-closed state, never a wildcard."
                    ),
                )
            ],
            as_of=as_of,
            tenant=tenant,
            market=market,
        )

    if pack.tenant != tenant or pack.market != market:
        return _verdict(
            [
                GateReason(
                    code=TENANT_MISMATCH,
                    outcome=GateOutcome.DENY,
                    detail=(
                        f"allowlist {pack.tenant}/{pack.market} was offered for "
                        f"{tenant}/{market}: one tenant's permissions never apply to another"
                    ),
                )
            ],
            as_of=as_of,
            tenant=tenant,
            market=market,
        )

    reasons: list[GateReason] = []
    citations: list[Citation] = []

    if intent is None or not intent.intent_id:
        reasons.append(
            GateReason(
                code=NO_INTENT_MATCH,
                outcome=GateOutcome.DENY,
                detail=(
                    "the utterance matched no allowlisted intent. Out of scope denies and hands "
                    "off; it never falls through to a general-purpose answer."
                ),
            )
        )
        return _verdict(reasons, as_of=as_of, tenant=tenant, market=market)

    spec = pack.intent(intent.intent_id)
    if spec is None:
        reasons.append(
            GateReason(
                code=NO_INTENT_MATCH,
                outcome=GateOutcome.DENY,
                detail=f"intent {intent.intent_id!r} is not in this tenant's allowlist",
            )
        )
        return _verdict(reasons, as_of=as_of, tenant=tenant, market=market, intent=intent)

    floor = spec.confidence_floor
    citations.append(
        Citation(
            source_id=f"allowlist:{pack.tenant}/{pack.market}#{spec.intent_id}",
            title=spec.title,
            snippet=f"confidence floor {floor}",
        )
    )
    if intent.confidence < floor:
        reasons.append(
            GateReason(
                code=BELOW_FLOOR,
                outcome=GateOutcome.DENY,
                detail=(
                    f"match quality {intent.confidence} is below the configured floor {floor} "
                    "for this intent (an ambiguous or weak match denies)"
                ),
            )
        )
    else:
        reasons.append(
            GateReason(
                code=INTENT_ALLOWED,
                outcome=GateOutcome.ALLOW,
                detail=f"intent {spec.intent_id!r} is allowlisted at {intent.confidence}",
            )
        )

    if requested_action:
        reasons.extend(
            _action_reasons(
                pack, spec_actions=spec.actions, action_id=requested_action, spec=action_spec
            )
        )

    return _verdict(
        reasons,
        as_of=as_of,
        tenant=tenant,
        market=market,
        intent=intent,
        floor=floor,
        action_id=requested_action,
        citations=citations,
    )


def _action_reasons(
    pack: AllowlistPack,
    *,
    spec_actions: Sequence[str],
    action_id: str,
    spec: ActionSpec | None,
) -> list[GateReason]:
    """The action half of the gate. Three separate permissions, all of which must hold."""
    reasons: list[GateReason] = []
    if action_id not in pack.allowed_actions:
        reasons.append(
            GateReason(
                code=ACTION_NOT_ALLOWLISTED,
                outcome=GateOutcome.DENY,
                detail=(
                    f"action {action_id!r} is not in the {pack.tenant}/{pack.market} action "
                    "allowlist"
                ),
            )
        )
    if action_id not in spec_actions:
        reasons.append(
            GateReason(
                code=ACTION_NOT_FOR_INTENT,
                outcome=GateOutcome.DENY,
                detail=(
                    f"action {action_id!r} is not reachable from this intent: an intent may only "
                    "request the actions its own entry names"
                ),
            )
        )
    if spec is None:
        reasons.append(
            GateReason(
                code=ACTION_UNKNOWN,
                outcome=GateOutcome.DENY,
                detail=f"action {action_id!r} is not declared by any action catalog",
            )
        )
        return reasons
    if spec.consequential:
        # NOT a denial: a consequential action is legitimate, it simply may not auto-execute.
        # REVIEW is what routes it to maker-checker instead of to the executor.
        reasons.append(
            GateReason(
                code=ACTION_CONSEQUENTIAL,
                outcome=GateOutcome.REVIEW,
                detail=(
                    f"action {action_id!r} is marked consequential in the catalog, so it goes to "
                    "maker-checker and never auto-executes"
                ),
            )
        )
    return reasons
