"""Action execution with maker-checker: the parameter check, and the line nothing crosses.

Four separate decisions, in this order, and the order is the control:

1. **The gate decided WHICH action** may be requested (``policy_gate``). This module never
   revisits that.
2. **This module validates the PARAMETERS** against the catalog's declared schema. A parameter
   the catalog did not declare, a required one that is absent, or a value that fails its
   declared pattern all stop the call here.
3. **Ownership decides WHOSE record was named.** A pattern proves shape and nothing else:
   ``[0-9]{4}`` cannot tell one customer's card from another's, and tenant partition cannot
   either, because two customers of one bank are one tenant. So every parameter the catalog
   marks ``binds_to_party`` is checked against the party the contact is about, and a value
   naming somebody else's record refuses and escalates.
4. **The catalog decides whether it may EXECUTE.** An action whose catalog metadata marks it
   ``consequential`` NEVER auto-executes, whatever the gate said, whoever asked, and however
   confident anything was. It yields a pending-review case and ZERO calls to the executor port.

That last property is asserted with a spy: the test counts adapter invocations, because "we do
not execute consequential actions" is a claim about a call that did not happen, and only
counting calls can prove it. An outcome object that merely SAYS ``executed=False`` while the
adapter ran is exactly the failure this design exists to prevent.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime

from .models import ActionCall, ActionOutcome, ActionSpec, GateOutcome, PolicyVerdict

__all__ = [
    "PARAMETER_NOT_OWNED",
    "ActionValidationError",
    "ExecutorPort",
    "decide",
    "unowned_parameters",
    "validate_parameters",
]

#: The refusal code for a parameter naming a record the caller was not shown to own. It is a
#: distinct code rather than a flavour of "invalid parameter" because the two are different
#: events: a malformed value is a mistake, and a well-formed value naming somebody else's
#: record is a request for another customer's data, which a human is meant to see.
PARAMETER_NOT_OWNED = "parameter_not_owned"


class ActionValidationError(ValueError):
    """The requested parameters do not satisfy the catalog's declared schema."""


class ExecutorPort:
    """Structural note, not a Protocol import.

    The real Protocol lives in ``ports/tool_catalog.py``; the domain must not import the ports
    package, so this module takes any object with an ``execute(call) -> ActionOutcome`` method
    and says so here. Keeping the domain free of the port package is what lets the whole engine
    be unit-tested with a two-line spy.
    """


def validate_parameters(spec: ActionSpec, parameters: Mapping[str, str]) -> dict[str, str]:
    """Return the validated parameter set, or raise.

    Unknown parameters are rejected rather than dropped. Silently discarding an argument the
    caller supplied is how a call ends up doing something other than what was asked, and the
    catalog is the schema of record.
    """
    declared = {parameter.name: parameter for parameter in spec.parameters}
    unknown = sorted(set(parameters) - set(declared))
    if unknown:
        raise ActionValidationError(
            f"action {spec.action_id!r}: parameters {unknown} are not declared by the catalog"
        )
    validated: dict[str, str] = {}
    for name, parameter in declared.items():
        raw = parameters.get(name)
        if raw is None or not str(raw).strip():
            if parameter.required:
                raise ActionValidationError(
                    f"action {spec.action_id!r}: required parameter {name!r} is missing"
                )
            continue
        value = str(raw)
        if parameter.pattern and not re.fullmatch(parameter.pattern, value):
            raise ActionValidationError(
                f"action {spec.action_id!r}: parameter {name!r} does not match the declared "
                f"pattern {parameter.pattern!r}"
            )
        validated[name] = value
    return validated


def unowned_parameters(
    spec: ActionSpec,
    validated: Mapping[str, str],
    ownership: Mapping[str, bool],
) -> tuple[str, ...]:
    """The supplied parameters that name a record the caller was NOT SHOWN to own.

    ``ownership`` is resolved by the caller, one lookup per parameter the catalog declares as
    binding to a party. A parameter missing from that mapping counts as not owned: "nobody
    checked" and "checked and it is theirs" must not land in the same branch, and the safe one
    of the two is the refusal. An adapter that cannot reach its records system raises rather
    than returning False, so a missing entry here means the caller skipped the lookup.
    """
    unowned = [
        parameter.name
        for parameter in spec.parameters
        if parameter.binds_to_party
        and parameter.name in validated
        and not ownership.get(parameter.name, False)
    ]
    return tuple(sorted(unowned))


def decide(
    spec: ActionSpec | None,
    call: ActionCall,
    verdict: PolicyVerdict,
    *,
    as_of: datetime,
    ownership: Mapping[str, bool] | None = None,
) -> tuple[bool, ActionOutcome]:
    """Decide whether ``call`` may execute, WITHOUT executing anything.

    Returns ``(may_execute, provisional_outcome)``. The caller executes only when the first
    element is True, so the decision and the side effect are separate statements in separate
    modules and a test can assert the decision with no adapter in the room at all.

    ``as_of`` is accepted and carried into the detail so a replay of the same case produces the
    same record; nothing here reads a clock.
    """
    if spec is None:
        return False, ActionOutcome(
            action_id=call.action_id,
            executed=False,
            detail=f"no catalog declares action {call.action_id!r}",
            requires_human_review=False,
        )
    if verdict.outcome is GateOutcome.DENY:
        return False, ActionOutcome(
            action_id=spec.action_id,
            executed=False,
            detail="the policy gate denied this turn, so no action is prepared",
            requires_human_review=False,
        )
    try:
        validated = validate_parameters(spec, call.parameters)
    except ActionValidationError as exc:
        return False, ActionOutcome(
            action_id=spec.action_id,
            executed=False,
            detail=str(exc),
            requires_human_review=False,
        )
    # Ownership is checked BEFORE the consequential line. Both refuse, but they refuse for
    # different reasons and only one of them is about the caller: "this is queued for
    # maker-checker" tells a reviewer nothing about the fact that somebody asked for another
    # customer's card, and that is the fact worth surfacing.
    unowned = unowned_parameters(spec, validated, ownership or {})
    if unowned:
        return False, ActionOutcome(
            action_id=spec.action_id,
            executed=False,
            detail=(
                f"{PARAMETER_NOT_OWNED}: {list(unowned)} name records that "
                f"{call.party_ref or 'an unidentified party'} was not shown to own"
            ),
            requires_human_review=True,
        )
    if spec.consequential:
        return False, ActionOutcome(
            action_id=spec.action_id,
            executed=False,
            detail=(
                f"action {spec.action_id!r} is consequential in the catalog: it is queued for "
                f"maker-checker as of {as_of.isoformat()} and never auto-executes"
            ),
            requires_human_review=True,
        )
    if verdict.outcome is not GateOutcome.ALLOW:
        return False, ActionOutcome(
            action_id=spec.action_id,
            executed=False,
            detail=f"the gate verdict is {verdict.outcome.value!r}, which is not permission",
            requires_human_review=True,
        )
    return True, ActionOutcome(
        action_id=spec.action_id,
        executed=False,
        detail="prepared",
        requires_human_review=False,
    )


def consequential_ids(specs: Sequence[ActionSpec]) -> tuple[str, ...]:
    """The action ids that may never auto-execute, for the runbook and the demo panel."""
    return tuple(sorted(spec.action_id for spec in specs if spec.consequential))
