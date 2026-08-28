"""Escalation is one table both modes read, and the customer-facing mode is not the lax one.

The defect this module exists for: ``self_service`` composed its own boolean from the disclosure
report and the action outcome and left ``degradation.review`` out of it, while ``assist_service``
included it. So a guardrail-BLOCKED turn escalated correctly in agent-assist, where a trained
employee is already on the contact, and escalated to NOBODY in self-service, where there is no
human in the room at all. The lower-risk mode was the careful one.

Neither service was wrong when read on its own. The defect lived in the gap between two
expressions of the same rule, which is why the rule is now a table in ``domain/escalation.py``
and both services call it. The tests below assert the fix, and then assert the property that
stops it recurring: self-service escalates wherever agent-assist does, and may escalate more.

Note that the property is an inequality rather than an equality. The two modes are ALLOWED to
differ, and on an unavailable screen they should, for the reason ``guardrails`` gives: a trained
employee is already on an agent-assist contact, so losing the model degrades the experience
without needing a reviewer. What must never happen is that asymmetry pointing the other way,
which is precisely what the defect was.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from contact_centre_conversations.config import build_container
from contact_centre_conversations.domain import escalation
from contact_centre_conversations.domain.guardrails import degradation_for
from contact_centre_conversations.domain.kernel import Severity
from contact_centre_conversations.domain.models import (
    ActionOutcome,
    DisclosureReport,
    DisclosureState,
    DisclosureStatus,
    ScreenOutcome,
    ScreenResult,
)
from contact_centre_conversations.domain.modes import ContactMode
from contact_centre_conversations.services import build_services

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)


def _clean_disclosures() -> DisclosureReport:
    return DisclosureReport(pack_id="p", market="SG", as_of=_AS_OF, statuses=())


def _missed_disclosures() -> DisclosureReport:
    return DisclosureReport(
        pack_id="p",
        market="SG",
        as_of=_AS_OF,
        statuses=(
            DisclosureStatus(
                disclosure_id="recording_notice",
                state=DisclosureState.MISSED,
                severity=Severity.HIGH,
                jurisdiction="MAS",
            ),
        ),
    )


def _screen(outcome: ScreenOutcome) -> ScreenResult:
    return ScreenResult(outcome=outcome, turn_index=0)


# ------------------------------------------------------------------ the defect, end to end
def test_a_blocked_turn_in_self_service_routes_to_a_human() -> None:
    """THE regression. This turn escalated to nobody in the customer-facing mode."""
    built = build_services(build_container(local_settings()))
    result = built.self_service.handle(
        sample_cases.INJECTION_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert result.screen.outcome is ScreenOutcome.BLOCKED
    assert result.requires_human_review is True
    # Rule R8: the flag is not the escalation, the routing is. An empty ref means nobody got it.
    assert result.review_ref


def test_a_blocked_turn_in_agent_assist_still_routes() -> None:
    """The half that was already right, kept honest so the fix cannot be a swap."""
    built = build_services(build_container(local_settings()))
    submission = replace(
        sample_cases.INJECTION_TURN,
        contact=replace(sample_cases.AGENT_CONTACT, mode=ContactMode.AGENT_ASSIST),
    )
    result = built.agent_assist.observe(submission, actor=sample_cases.ACTOR, as_of=_AS_OF)
    assert result.screen.outcome is ScreenOutcome.BLOCKED
    assert result.requires_human_review is True
    assert result.review_ref


# ------------------------------------------------------------------ the property that holds it
def test_the_customer_facing_mode_is_never_the_laxer_one_about_a_screen_event() -> None:
    """The gap the defect lived in, closed as a property rather than as two fixed booleans.

    The two modes are ALLOWED to differ, and on an unavailable screen they should: agent-assist
    has a trained employee already on the contact, so losing the model degrades the experience
    without needing a reviewer, while self-service has nobody and the same event is a reason to
    fetch somebody. That asymmetry is deliberate and documented in ``guardrails``.

    What must never happen is the asymmetry pointing the other way. So the property is not
    "the modes agree" but "self-service escalates wherever agent-assist does, and may escalate
    more". The defect was exactly this inequality inverted: the mode with no human in the room
    was the one that told nobody.
    """
    for outcome in (ScreenOutcome.BLOCKED, ScreenOutcome.UNAVAILABLE):
        screen = _screen(outcome)
        escalates = {
            mode: bool(
                escalation.reasons_for(
                    degradation=degradation_for(mode, screen),
                    disclosures=_clean_disclosures(),
                )
            )
            for mode in (ContactMode.AGENT_ASSIST, ContactMode.SELF_SERVICE)
        }
        assert not (
            escalates[ContactMode.AGENT_ASSIST] and not escalates[ContactMode.SELF_SERVICE]
        ), (
            f"a {outcome.value} screen escalates for agent-assist but not for self-service. "
            "The customer-facing mode has no human in the room; it cannot be the lax one."
        )


# ------------------------------------------------------------------ the table itself
def test_a_clean_turn_escalates_for_no_reason_at_all() -> None:
    reasons = escalation.reasons_for(
        degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.CLEAN)),
        disclosures=_clean_disclosures(),
        action=None,
    )
    assert reasons == ()


def test_a_screen_failure_names_the_screen_reason() -> None:
    reasons = escalation.reasons_for(
        degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.BLOCKED)),
        disclosures=_clean_disclosures(),
    )
    assert [reason.code for reason in reasons] == [escalation.SCREEN_DEGRADED]


def test_an_action_needing_review_names_the_action_reason_and_carries_its_detail() -> None:
    """The engine's own words travel with the reason, rather than being re-derived here."""
    action = ActionOutcome(
        action_id="block_card",
        executed=False,
        detail="parameter_not_owned: ['card_last4'] name records ...",
        requires_human_review=True,
    )
    reasons = escalation.reasons_for(
        degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.CLEAN)),
        disclosures=_clean_disclosures(),
        action=action,
    )
    assert [reason.code for reason in reasons] == [escalation.ACTION_REVIEW]
    assert "parameter_not_owned" in reasons[0].detail


def test_every_reason_that_applies_is_reported_not_just_the_first() -> None:
    """A reviewer told only the first reason would close the case having seen one of three."""
    action = ActionOutcome(
        action_id="block_card", executed=False, detail="consequential", requires_human_review=True
    )
    reasons = escalation.reasons_for(
        degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.BLOCKED)),
        disclosures=_missed_disclosures(),
        action=action,
    )
    assert [reason.code for reason in reasons] == [
        escalation.MISSED_DISCLOSURE,
        escalation.SCREEN_DEGRADED,
        escalation.ACTION_REVIEW,
    ]


def test_the_order_is_stable_so_two_reviewers_read_the_same_case_alike() -> None:
    action = ActionOutcome(
        action_id="block_card", executed=False, detail="consequential", requires_human_review=True
    )
    calls = [
        escalation.reasons_for(
            degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.BLOCKED)),
            disclosures=_missed_disclosures(),
            action=action,
        )
        for _ in range(3)
    ]
    assert calls[0] == calls[1] == calls[2]


def test_an_action_that_executed_cleanly_escalates_nothing() -> None:
    action = ActionOutcome(
        action_id="read_card_balance", executed=True, detail="executed", requires_human_review=False
    )
    reasons = escalation.reasons_for(
        degradation=degradation_for(ContactMode.SELF_SERVICE, _screen(ScreenOutcome.CLEAN)),
        disclosures=_clean_disclosures(),
        action=action,
    )
    assert reasons == ()
