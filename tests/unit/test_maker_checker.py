"""Maker-checker: a consequential action yields a pending review and ZERO adapter calls.

The claim under test is about something that did NOT happen, so the only honest proof is to
COUNT. A spy executor wraps the real fixture catalog and records every invocation; an outcome
object that merely says ``executed=False`` while the adapter ran would pass a weaker test and
fail this one.

The second half is as important: a NON-consequential action must actually execute and land in
the audit trail, because a service that refuses everything is trivially safe and useless, and a
maker-checker control nobody can distinguish from an outage is not a control.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contact_centre_conversations.adapters.local.tool_catalog import (
    LocalFixtureToolCatalog,
)
from contact_centre_conversations.config import (
    build_container,
)
from contact_centre_conversations.domain import (
    action_engine,
)
from contact_centre_conversations.domain.models import (
    ActionCall,
    ActionOutcome,
    ActionSpec,
    GateOutcome,
    ParameterSpec,
    PolicyVerdict,
)
from contact_centre_conversations.services import (
    build_services,
)

from tests.conftest import SHIPPED_PACKS, local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


class SpyToolCatalog:
    """The real fixture catalog, with every execute() call counted."""

    def __init__(self, settings: object) -> None:
        self._inner = LocalFixtureToolCatalog(settings)  # type: ignore[arg-type]
        self.executed: list[ActionCall] = []

    def describe(self, action_id: str) -> ActionSpec | None:
        return self._inner.describe(action_id)

    def execute(self, call: ActionCall) -> ActionOutcome:
        self.executed.append(call)
        return self._inner.execute(call)

    @property
    def calls(self) -> tuple[ActionCall, ...]:
        return tuple(self.executed)


def _verdict(outcome: GateOutcome, action_id: str) -> PolicyVerdict:
    return PolicyVerdict(
        outcome=outcome,
        as_of=_AS_OF,
        tenant=sample_cases.TENANT,
        market=sample_cases.MARKET,
        intent_id="report_lost_card",
        action_id=action_id,
    )


def _services(spy: SpyToolCatalog) -> object:
    settings = local_settings()
    container = build_container(settings)
    built = build_services(container)
    # Rebind the tool port on the constructed service: the point is to count what the ENGINE
    # asks the adapter to do, so the spy has to sit exactly where the adapter sits.
    built.self_service._tools = spy  # type: ignore[attr-defined]  # noqa: SLF001
    return built


@pytest.mark.parametrize(
    ("action_id", "text", "parameters"),
    [
        ("block_card", "I lost my card yesterday.", {"card_last4": "4321"}),
        (
            "raise_chargeback",
            "There is an unauthorised transaction on my statement.",
            {"card_last4": "4321", "transaction_ref": "TXN-000001"},
        ),
    ],
)
def test_a_consequential_action_never_reaches_the_executor(
    action_id: str, text: str, parameters: dict[str, str]
) -> None:
    spy = SpyToolCatalog(local_settings())
    built = _services(spy)
    result = built.self_service.handle(  # type: ignore[attr-defined]
        sample_cases.TurnSubmission(
            contact=sample_cases.CUSTOMER_CONTACT,
            index=0,
            speaker_id="customer",
            role=sample_cases.ChannelRole.CUSTOMER,
            text=text,
        ),
        actor=sample_cases.ACTOR,
        as_of=_AS_OF,
        requested_action=action_id,
        parameters=parameters,
    )
    assert result.verdict.outcome is GateOutcome.REVIEW, (
        "the gate must reach REVIEW for a consequential action, not DENY: a denial would make "
        "this test pass for the wrong reason, because nothing is prepared after a denial"
    )
    assert spy.executed == [], (
        f"the executor was called for {action_id!r}, which the catalog marks consequential: "
        "an outcome that says executed=False while the adapter ran is the exact defect this "
        "test exists to catch"
    )
    assert result.action is not None
    assert result.action.executed is False
    assert result.action.requires_human_review is True
    assert result.action.review_ref, "a pending-review case with no review reference went nowhere"


def test_a_non_consequential_action_executes_and_is_recorded() -> None:
    spy = SpyToolCatalog(local_settings())
    built = _services(spy)
    result = built.self_service.handle(  # type: ignore[attr-defined]
        sample_cases.IN_SCOPE_TURN,
        actor=sample_cases.ACTOR,
        as_of=_AS_OF,
        requested_action="read_card_balance",
        parameters={"card_last4": "4321"},
    )
    assert [call.action_id for call in spy.executed] == ["read_card_balance"]
    assert result.action is not None
    assert result.action.executed is True
    assert result.action.reference, "an executed action with no reference cannot be traced"


def test_the_adapter_itself_refuses_a_consequential_action_as_a_second_wall() -> None:
    """The engine must never route one here. If a refactor did, this is what stops it."""
    catalog = LocalFixtureToolCatalog(local_settings())
    with pytest.raises(PermissionError, match="consequential"):
        catalog.execute(
            ActionCall(
                action_id="block_card",
                contact_id=sample_cases.SELF_SERVICE_CONTACT_ID,
                tenant=sample_cases.TENANT,
                parameters={"card_last4": "4321"},
            )
        )


# --------------------------------------------------------------------------- #
# Parameter validation against the catalog schema
# --------------------------------------------------------------------------- #
def _spec() -> ActionSpec:
    spec = SHIPPED_PACKS.action_spec("read_card_balance")
    assert spec is not None
    return spec


def test_a_missing_required_parameter_stops_the_call() -> None:
    with pytest.raises(action_engine.ActionValidationError, match="required"):
        action_engine.validate_parameters(_spec(), {})


def test_a_parameter_failing_its_declared_pattern_stops_the_call() -> None:
    with pytest.raises(action_engine.ActionValidationError, match="pattern"):
        action_engine.validate_parameters(_spec(), {"card_last4": "not-four-digits"})


def test_an_undeclared_parameter_is_rejected_rather_than_dropped() -> None:
    """Silently discarding an argument is how a call does something other than what was asked."""
    with pytest.raises(action_engine.ActionValidationError, match="not declared"):
        action_engine.validate_parameters(_spec(), {"card_last4": "4321", "amount": "9999"})


def test_a_denied_gate_prepares_no_action_at_all() -> None:
    may_execute, outcome = action_engine.decide(
        _spec(),
        ActionCall(
            action_id="read_card_balance",
            contact_id="c1",
            tenant=sample_cases.TENANT,
            parameters={"card_last4": "4321"},
        ),
        _verdict(GateOutcome.DENY, "read_card_balance"),
        as_of=_AS_OF,
    )
    assert may_execute is False
    assert outcome.requires_human_review is False


def test_an_unknown_parameter_spec_still_validates_optional_absence() -> None:
    spec = ActionSpec(
        action_id="probe",
        title="Probe",
        consequential=False,
        parameters=(ParameterSpec(name="note", required=False),),
    )
    assert action_engine.validate_parameters(spec, {}) == {}
