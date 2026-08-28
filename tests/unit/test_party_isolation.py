"""One customer must not reach another customer's records, and tenant partition cannot say so.

The cross-tenant proofs in ``test_handoff_and_persistence.py`` are real, and they are not this.
They separate demo-bank from rival-bank. This module separates two customers OF demo-bank, which
is the case tenant partition is structurally unable to see: ``party-sg-0001`` and
``party-sg-0002`` share a tenant, a market, a vertical and an allowlist, and the only thing that
distinguishes their cards is who owns them.

Before ownership existed, ``card_last4`` was validated against ``[0-9]{4}`` and nothing else, so
an allowed intent plus four well-formed digits read whichever card was named. That is the defect
here, held shut from three directions:

* a customer naming ANOTHER customer's record is refused and escalated, not quietly denied;
* the same customer naming their OWN record still works, or the check above would be satisfied
  by an adapter that simply refused everybody;
* a contact with nobody identified reaches no records at all, because a party nobody verified
  owns nothing.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from contact_centre_conversations.config import build_container
from contact_centre_conversations.domain.action_engine import PARAMETER_NOT_OWNED
from contact_centre_conversations.services import build_services

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)

#: The non-consequential read. Consequential actions never execute anyway, so proving ownership
#: on one of those would prove nothing: the maker-checker line would have stopped it regardless.
_READ = "read_card_balance"
#: The card `config/parties/records.jsonl` gives to party-sg-0001, and the one it gives to
#: party-sg-0002. Same tenant, same shape, different owner.
_OWN_CARD = {"card_last4": "4321"}
_OTHER_CUSTOMERS_CARD = {"card_last4": "9876"}


def _handle(party_ref: str, parameters: dict[str, str]):
    """Run one allowed self-service turn as ``party_ref``, asking for ``parameters``."""
    container = build_container(local_settings())
    built = build_services(container)
    contact = replace(sample_cases.CUSTOMER_CONTACT, party_ref=party_ref)
    submission = replace(sample_cases.IN_SCOPE_TURN, contact=contact)
    result = built.self_service.handle(
        submission,
        actor=sample_cases.ACTOR,
        as_of=_AS_OF,
        requested_action=_READ,
        parameters=parameters,
    )
    return result, container


def test_a_customer_may_read_their_own_record() -> None:
    """The green case. Without it every assertion below is satisfied by refusing everybody."""
    result, _ = _handle(sample_cases.PARTY_REF, _OWN_CARD)
    assert result.action is not None
    assert result.action.executed is True


def test_a_customer_may_not_read_another_customers_record_in_the_same_tenant() -> None:
    """The defect itself: same tenant, same allowlist, same parameter shape, different owner."""
    result, _ = _handle(sample_cases.PARTY_REF, _OTHER_CUSTOMERS_CARD)
    assert result.action is not None
    assert result.action.executed is False
    assert PARAMETER_NOT_OWNED in result.action.detail


def test_the_refusal_escalates_rather_than_denying_quietly() -> None:
    """Somebody asked for another customer's data. That is for a human to see, not a log line."""
    result, _ = _handle(sample_cases.PARTY_REF, _OTHER_CUSTOMERS_CARD)
    assert result.action is not None
    assert result.action.requires_human_review is True
    assert result.requires_human_review is True
    # Rule R8: the flag and the routing are one act, so the reference says where it WENT.
    assert result.review_ref


def test_the_refusal_reaches_the_executor_zero_times() -> None:
    """A refusal that ran the read and discarded the answer has already disclosed the record."""
    result, container = _handle(sample_cases.PARTY_REF, _OTHER_CUSTOMERS_CARD)
    assert result.action is not None and result.action.executed is False
    assert container.tool_catalog.calls == ()


def test_an_unidentified_contact_reaches_nobodys_records() -> None:
    """A contact begins before anyone is verified, and until then nobody owns anything."""
    result, _ = _handle("", _OWN_CARD)
    assert result.action is not None
    assert result.action.executed is False
    assert PARAMETER_NOT_OWNED in result.action.detail


def test_the_other_customer_may_read_the_record_that_is_theirs() -> None:
    """Ownership is a property of the pair, not a blocklist on one value.

    Without this, a fixture that simply never returned 9876 to anybody would pass every test
    above while modelling nothing.
    """
    result, _ = _handle(sample_cases.OTHER_PARTY_REF, _OTHER_CUSTOMERS_CARD)
    assert result.action is not None
    assert result.action.executed is True


def test_the_same_digits_under_another_tenant_are_a_different_record() -> None:
    """rival-bank's party-rb-0001 also holds a card ending 4321. It is not demo-bank's."""
    container = build_container(local_settings())
    records = container.party_records
    assert records.owns(
        party_ref="party-rb-0001", tenant="rival-bank", parameter="card_last4", value="4321"
    )
    assert not records.owns(
        party_ref="party-rb-0001",
        tenant=sample_cases.TENANT,
        parameter="card_last4",
        value="4321",
    )


def test_the_same_value_under_another_parameter_is_a_different_record() -> None:
    """Ownership is keyed by what the value NAMES, not by the digits alone."""
    container = build_container(local_settings())
    records = container.party_records
    assert not records.owns(
        party_ref=sample_cases.PARTY_REF,
        tenant=sample_cases.TENANT,
        parameter="transaction_ref",
        value="4321",
    )


def test_an_unreadable_records_fixture_raises_rather_than_refusing_everybody() -> None:
    """ "We could not check" and "it is not theirs" are different facts, and only one is a denial.

    An adapter that returned False when its records system was unreachable would tell every
    customer their own card is not theirs, and would look like a working deployment while doing
    it.
    """
    container = build_container(local_settings(parties_path="/nonexistent/records.jsonl"))
    with pytest.raises(RuntimeError, match="does not exist"):
        container.party_records.owns(
            party_ref=sample_cases.PARTY_REF,
            tenant=sample_cases.TENANT,
            parameter="card_last4",
            value="4321",
        )
