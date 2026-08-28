"""Handoff carry-over, grounded suggestion discipline, tenant isolation and audit completeness.

Four claims that only hold end to end:

* a handoff carries enough that the RESUMED engine lands on the same state (replayed, not
  assigned);
* a suggestion exists only when a retrieved passage supports it, and is discarded on any
  schema, citation or grounding failure;
* a cross-tenant read answers 403 and not 404, and the check is what makes it do so;
* every verdict, reminder, suggestion, gate decision, action and handoff lands in the audit
  trail, tagged with the mode.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contact_centre_conversations.adapters.local.retrieval import (
    LocalFixtureRetrievalAdapter,
)
from contact_centre_conversations.config import (
    build_container,
)
from contact_centre_conversations.domain import (
    handoff,
    procedure_engine,
    suggestions,
)
from contact_centre_conversations.domain.errors import (
    TenantMismatchError,
)
from contact_centre_conversations.domain.kernel import (
    Citation,
)
from contact_centre_conversations.domain.models import (
    ContactRef,
    HandoffTrigger,
    RetrievedPassage,
    TurnSubmission,
)
from contact_centre_conversations.domain.modes import (
    ContactMode,
)
from contact_centre_conversations.services import (
    build_services,
)

from tests.conftest import SHIPPED_PACKS, local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
_PROCEDURE = SHIPPED_PACKS.procedure_for("SG", sample_cases.VERTICAL)
assert _PROCEDURE is not None


# --------------------------------------------------------------------------- #
# Handoff carry-over
# --------------------------------------------------------------------------- #
def _self_service_contact() -> ContactRef:
    return sample_cases.CUSTOMER_CONTACT


def test_a_denied_turn_produces_a_complete_handoff_package() -> None:
    built = build_services(build_container(local_settings()))
    result = built.self_service.handle(
        sample_cases.OUT_OF_SCOPE_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    package = result.handoff
    assert package is not None
    assert package.trigger is HandoffTrigger.GATE_DENIAL
    assert package.gate_verdicts, "a gate-denial handoff must carry the verdicts that denied it"
    assert package.turns, "a handoff with no transcript makes the customer start again"
    assert package.tenant == sample_cases.TENANT


def test_the_resumed_engine_state_matches_what_was_carried_over() -> None:
    """The replay assertion: the receiving side derives, it does not accept an assigned state."""
    transcript = sample_cases.transcript(
        "Thank you for calling.",
        "Please confirm your date of birth.",
        "Which merchant was it?",
    )
    handed = procedure_engine.advance(_PROCEDURE, transcript, as_of=_AS_OF)
    resumed = procedure_engine.replay_carry_over(
        _PROCEDURE, transcript, as_of=_AS_OF, carry_over=handed.carry_over
    )
    assert resumed.state_id == handed.state_id
    assert resumed.completed_state_ids == handed.completed_state_ids
    assert resumed.carry_over == handed.carry_over


def test_a_package_with_no_transcript_is_refused_by_its_own_producer() -> None:
    with pytest.raises(handoff.HandoffValidationError, match="transcript"):
        handoff.build_package(
            _self_service_contact(),
            trigger=HandoffTrigger.CUSTOMER_REQUEST,
            redacted_turns=(),
            progress=None,
            verdicts=(),
            created_at=_AS_OF,
        )


def test_an_explicit_request_for_a_person_beats_a_policy_code() -> None:
    cues = SHIPPED_PACKS.cues_for("SG", sample_cases.VERTICAL)
    assert cues is not None
    trigger = handoff.decide_trigger(
        verdict=None,
        consecutive_failures=0,
        customer_text="I want to speak to a person please",
        escalation_lexicon=cues.escalation,
        vulnerability_lexicon=cues.vulnerability,
    )
    assert trigger is HandoffTrigger.CUSTOMER_REQUEST


def test_a_vulnerability_cue_wins_over_everything_but_a_screen_failure() -> None:
    cues = SHIPPED_PACKS.cues_for("SG", sample_cases.VERTICAL)
    assert cues is not None
    trigger = handoff.decide_trigger(
        verdict=None,
        consecutive_failures=5,
        customer_text="I am struggling to pay this month and I want to speak to a person",
        escalation_lexicon=cues.escalation,
        vulnerability_lexicon=cues.vulnerability,
    )
    assert trigger is HandoffTrigger.VULNERABILITY


# --------------------------------------------------------------------------- #
# Grounded suggestion discipline
# --------------------------------------------------------------------------- #
def _passage(text: str = "The investigation takes up to 45 days.") -> RetrievedPassage:
    return RetrievedPassage(
        text=text, citation=Citation(source_id="kb-sg-004", title="Disputed transactions")
    )


def test_empty_retrieval_means_no_suggestion() -> None:
    assert (
        suggestions.validate_draft(
            {"text": "anything", "passage_ids": ["kb-sg-004"]},
            (),
            mode=ContactMode.AGENT_ASSIST,
        )
        is None
    )


@pytest.mark.parametrize(
    "payload",
    [
        "not an object",
        {"passage_ids": ["kb-sg-004"]},
        {"text": "", "passage_ids": ["kb-sg-004"]},
        {"text": "ok", "passage_ids": []},
        {"text": "ok", "passage_ids": "kb-sg-004"},
        {"text": "ok", "passage_ids": [7]},
        {"text": "x" * 400, "passage_ids": ["kb-sg-004"]},
    ],
)
def test_any_schema_failure_discards_the_whole_draft(payload: object) -> None:
    assert suggestions.validate_draft(payload, (_passage(),), mode=ContactMode.AGENT_ASSIST) is None


def test_a_citation_that_was_never_retrieved_discards_the_draft() -> None:
    """Fabricated provenance is worse than fabricated text, because it looks checked."""
    assert (
        suggestions.validate_draft(
            {"text": "See the policy.", "passage_ids": ["kb-sg-999"]},
            (_passage(),),
            mode=ContactMode.AGENT_ASSIST,
        )
        is None
    )


def test_a_figure_the_passages_do_not_contain_discards_the_draft() -> None:
    """The model never produces a number; a number it produced anyway is caught here."""
    assert (
        suggestions.validate_draft(
            {"text": "The investigation takes up to 90 days.", "passage_ids": ["kb-sg-004"]},
            (_passage(),),
            mode=ContactMode.AGENT_ASSIST,
        )
        is None
    )


def test_a_grounded_cited_draft_survives() -> None:
    reply = suggestions.validate_draft(
        {"text": "The investigation takes up to 45 days.", "passage_ids": ["kb-sg-004"]},
        (_passage(),),
        mode=ContactMode.AGENT_ASSIST,
    )
    assert reply is not None
    assert reply.citations and reply.passage_ids == ("kb-sg-004",)


def test_the_offline_corpus_refuses_rather_than_answering_from_nothing(tmp_path: object) -> None:
    """An unreachable index reported as an empty result would look like a quiet knowledge base."""
    adapter = LocalFixtureRetrievalAdapter(local_settings(kb_path=""))
    with pytest.raises(RuntimeError, match="ground nothing"):
        adapter.retrieve(
            suggestions.build_query(
                "anything", market="SG", locale="en-SG", mode=ContactMode.AGENT_ASSIST
            )
        )


def test_a_suggestion_is_suppressed_when_retrieval_returns_nothing() -> None:
    built = build_services(build_container(local_settings()))
    result = built.agent_assist.observe(
        TurnSubmission(
            contact=sample_cases.AGENT_CONTACT,
            index=0,
            speaker_id="agent-1",
            role=sample_cases.ChannelRole.AGENT,
            text="Zzzzz qqqqq wwwww vvvvv.",
            start_ms=0,
            end_ms=2000,
        ),
        actor=sample_cases.ACTOR,
        as_of=_AS_OF,
    )
    assert result.suggestion is None, "no passage, no suggestion"


# --------------------------------------------------------------------------- #
# Tenant isolation: 403, not 404
# --------------------------------------------------------------------------- #
def test_a_cross_tenant_read_is_refused_rather_than_reported_as_missing() -> None:
    container = build_container(local_settings())
    store = container.contact_store
    store.create(sample_cases.AGENT_CONTACT)
    with pytest.raises(TenantMismatchError) as excinfo:
        store.contact(sample_cases.CLEAN_CONTACT_ID, tenant=sample_cases.OTHER_TENANT)
    assert excinfo.value.http_status == 403, (
        "a cross-tenant read must answer 403: a contact id is not a secret in this vertical, "
        "so 404 would only cost an operator the difference between 'not yours' and 'lost'"
    )


def test_an_id_that_belongs_to_nobody_is_simply_absent() -> None:
    container = build_container(local_settings())
    assert container.contact_store.contact("no-such-contact", tenant=sample_cases.TENANT) is None


def test_a_cross_tenant_write_is_refused_too() -> None:
    container = build_container(local_settings())
    container.contact_store.create(sample_cases.AGENT_CONTACT)
    stolen = ContactRef(
        contact_id=sample_cases.CLEAN_CONTACT_ID,
        tenant=sample_cases.OTHER_TENANT,
        market=sample_cases.MARKET,
        locale=sample_cases.LOCALE,
        vertical=sample_cases.VERTICAL,
        mode=ContactMode.AGENT_ASSIST,
    )
    with pytest.raises(TenantMismatchError):
        container.contact_store.create(stolen)


# --------------------------------------------------------------------------- #
# Audit completeness, tagged with the mode
# --------------------------------------------------------------------------- #
def test_every_turn_of_both_modes_lands_in_the_audit_trail_tagged_with_its_mode() -> None:
    container = build_container(local_settings())
    built = build_services(container)
    built.agent_assist.observe(sample_cases.OPENING_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF)
    built.self_service.handle(sample_cases.IN_SCOPE_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF)

    records = container.audit.log.read_all()
    modes = [record["mode"] for record in records]
    assert modes == ["agent_assist", "self_service"], (
        "an audit record with no mode cannot be counted towards either mode's promotion "
        "evidence, which is the whole reason the modes are gated apart"
    )
    for record in records:
        assert record["tenant"] == sample_cases.TENANT
        assert record["contact_id"]
        assert record["redacted_summary"]
    assert container.audit.verify().ok


def test_the_audit_summary_carries_the_facts_and_not_the_turn_text() -> None:
    container = build_container(local_settings())
    built = build_services(container)
    built.agent_assist.observe(sample_cases.PII_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF)
    summary = container.audit.log.read_all()[-1]["redacted_summary"]
    assert sample_cases.PLANTED_NRIC not in summary
    assert "state=" in summary and "screen=" in summary
