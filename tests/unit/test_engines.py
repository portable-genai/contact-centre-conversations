"""The deterministic engines: procedure, disclosure timing, intent scoring and the policy gate.

Every consequential number and verdict in E1 comes from one of these four, and none of them may
consult a model. The suite drives the SHIPPED packs rather than bespoke fixtures, so a pack edit
that breaks an engine fails here instead of passing against a pack nobody deploys.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from speech_lexicon_kit import ChannelRole, SpeakerTurn, Transcript, find_hits

from contact_centre_conversations.domain import (
    disclosure_engine,
    intent_engine,
    policy_gate,
    procedure_engine,
)
from contact_centre_conversations.domain.models import (
    DisclosureState,
    GateOutcome,
    IntentMatch,
)
from contact_centre_conversations.domain.packs import (
    PackError,
    PackLibrary,
)

from tests.conftest import SHIPPED_PACKS
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)
_PROCEDURE = SHIPPED_PACKS.procedure_for("SG", sample_cases.VERTICAL)
_DISCLOSURES = SHIPPED_PACKS.disclosure_for("SG", sample_cases.VERTICAL)
_ALLOWLIST = SHIPPED_PACKS.allowlist_for("demo-bank", "SG", sample_cases.VERTICAL)
assert _PROCEDURE is not None and _DISCLOSURES is not None and _ALLOWLIST is not None


def _transcript(*rows: tuple[str, int, int]) -> Transcript:
    return Transcript(
        transcript_id="t-engine",
        locale=sample_cases.LOCALE,
        turns=tuple(
            SpeakerTurn(
                index=index,
                speaker_id="agent-1",
                role=ChannelRole.AGENT,
                text=text,
                start_ms=start,
                end_ms=end,
            )
            for index, (text, start, end) in enumerate(rows)
        ),
    )


# --------------------------------------------------------------------------- #
# Procedure and next best step
# --------------------------------------------------------------------------- #
def test_a_contact_with_nothing_said_sits_at_the_initial_state() -> None:
    progress = procedure_engine.advance(_PROCEDURE, _transcript(("Hello.", 0, 1000)), as_of=_AS_OF)
    assert progress.state_id == _PROCEDURE.initial_state
    assert progress.completed_state_ids == ()
    assert progress.missing_evidence == ("greeting_given", "greeting_given")


def test_the_engine_advances_only_on_evidence_and_emits_one_step() -> None:
    progress = procedure_engine.advance(
        _PROCEDURE,
        _transcript(
            ("Thank you for calling.", 0, 3000),
            ("Please confirm your date of birth.", 4000, 8000),
        ),
        as_of=_AS_OF,
    )
    assert progress.completed_state_ids == ("greeting", "verify_identity")
    assert progress.state_id == "take_dispute"
    step = procedure_engine.next_best_step(_PROCEDURE, progress)
    assert step.state_id == "take_dispute"
    assert step.instruction == _PROCEDURE.state("take_dispute").instruction, (
        "the instruction must be the pack author's sentence, not one this code composed"
    )
    assert step.citations, "a next step with no citation cannot be traced to a reviewed pack"


def test_the_customer_cannot_satisfy_the_agent_s_evidence() -> None:
    """A state names a speaker; a caller reciting the script is not the agent following it."""
    transcript = Transcript(
        transcript_id="t-role",
        locale=sample_cases.LOCALE,
        turns=(
            SpeakerTurn(
                index=0,
                speaker_id="customer",
                role=ChannelRole.CUSTOMER,
                text="Thank you for calling, you are supposed to say.",
                start_ms=0,
                end_ms=3000,
            ),
        ),
    )
    progress = procedure_engine.advance(_PROCEDURE, transcript, as_of=_AS_OF)
    assert progress.state_id == "greeting"


def test_the_walk_is_byte_identical_across_replays() -> None:
    transcript = _transcript(
        ("Thank you for calling.", 0, 3000),
        ("Please confirm your date of birth.", 4000, 8000),
    )
    first = procedure_engine.advance(_PROCEDURE, transcript, as_of=_AS_OF)
    second = procedure_engine.advance(_PROCEDURE, transcript, as_of=_AS_OF)
    assert first == second


def test_a_completed_procedure_says_so_rather_than_repeating_the_last_step() -> None:
    progress = procedure_engine.advance(
        _PROCEDURE,
        _transcript(
            ("Thank you for calling.", 0, 3000),
            ("Please confirm your date of birth.", 4000, 8000),
            ("Which merchant was it?", 9000, 12000),
            ("I have blocked the card.", 13000, 16000),
            ("You will receive a replacement card.", 17000, 20000),
        ),
        as_of=_AS_OF,
    )
    assert progress.complete is True
    assert procedure_engine.next_best_step(_PROCEDURE, progress).instruction.startswith(
        "The procedure is complete"
    )


# --------------------------------------------------------------------------- #
# Disclosure timing
# --------------------------------------------------------------------------- #
def _report(transcript: Transcript, *, contact_ended: bool = False) -> object:
    progress = procedure_engine.advance(_PROCEDURE, transcript, as_of=_AS_OF)
    return disclosure_engine.evaluate_disclosures(
        _DISCLOSURES,
        transcript,
        as_of=_AS_OF,
        progress=progress,
        procedure_hits=find_hits(transcript, _PROCEDURE.lexicon),
        contact_ended=contact_ended,
    )


def _state(report: object, disclosure_id: str) -> DisclosureState:
    return next(
        status.state
        for status in report.statuses  # type: ignore[attr-defined]
        if status.disclosure_id == disclosure_id
    )


def test_a_disclosure_inside_its_window_is_satisfied() -> None:
    report = _report(_transcript(("This call is being recorded for quality.", 0, 6000)))
    assert _state(report, "recording_notice") is DisclosureState.SATISFIED


def test_a_disclosure_after_its_window_is_missed() -> None:
    report = _report(_transcript(("This call is being recorded.", 60_000, 66_000)))
    assert _state(report, "recording_notice") is DisclosureState.MISSED


def test_an_open_window_on_a_live_contact_is_pending_and_due() -> None:
    report = _report(_transcript(("Thank you for calling.", 0, 3000)))
    assert _state(report, "recording_notice") is DisclosureState.PENDING
    assert [status.disclosure_id for status in report.due] == ["recording_notice"]  # type: ignore[attr-defined]


def test_a_window_that_closes_at_contact_end_is_missed_and_demands_review() -> None:
    report = _report(_transcript(("Thank you for calling.", 0, 3000)), contact_ended=True)
    assert _state(report, "recording_notice") is DisclosureState.MISSED
    assert report.requires_human_review is True  # type: ignore[attr-defined]


def test_a_reminder_never_fires_before_its_trigger() -> None:
    """The dispute-rights notice triggers on a lexicon hit that has not happened yet."""
    report = _report(_transcript(("Thank you for calling.", 0, 3000)))
    status = next(
        s
        for s in report.statuses
        if s.disclosure_id == "dispute_rights_notice"  # type: ignore[attr-defined]
    )
    assert status.due_from_ms is None
    assert status.is_due is False


def test_a_transcript_with_no_timings_is_unverifiable_rather_than_satisfied() -> None:
    """An absent clock cannot answer a timing question in either direction."""
    transcript = Transcript(
        transcript_id="t-untimed",
        locale=sample_cases.LOCALE,
        turns=(
            SpeakerTurn(
                index=0,
                speaker_id="agent-1",
                role=ChannelRole.AGENT,
                text="This call is being recorded.",
            ),
        ),
    )
    report = _report(transcript)
    assert _state(report, "recording_notice") is DisclosureState.UNVERIFIABLE


# --------------------------------------------------------------------------- #
# Intent scoring
# --------------------------------------------------------------------------- #
def test_a_clean_unique_match_scores_one() -> None:
    match = intent_engine.best_intent(_ALLOWLIST, "what is my card balance please")
    assert match is not None
    assert match.intent_id == "card_balance"
    assert match.confidence == 1.0


def test_an_out_of_scope_ask_matches_nothing() -> None:
    assert intent_engine.best_intent(_ALLOWLIST, "please refinance my mortgage") is None


def test_a_contested_utterance_scores_below_a_unique_one() -> None:
    """Two allowlisted intents fitting an utterance is what distinctness is there to punish."""
    contested = intent_engine.best_intent(_ALLOWLIST, "card balance and my last transactions")
    assert contested is not None
    assert contested.confidence < 1.0


def test_a_contested_utterance_falls_below_a_high_floor() -> None:
    """The dispute intent carries a 0.8 floor, so a contested match for it denies.

    The gate is what turns this into a refusal; the engine's job is only to report that the
    match was contested, which is what the score below 0.8 says.
    """
    match = intent_engine.best_intent(_ALLOWLIST, "unauthorised transaction, and my card balance")
    assert match is not None
    assert match.intent_id == "dispute_transaction"
    spec = _ALLOWLIST.intent("dispute_transaction")
    assert spec is not None
    assert match.confidence < spec.confidence_floor


# --------------------------------------------------------------------------- #
# The policy gate
# --------------------------------------------------------------------------- #
def _evaluate(pack: object, **kwargs: object) -> object:
    return policy_gate.evaluate(
        pack,  # type: ignore[arg-type]
        tenant="demo-bank",
        market="SG",
        as_of=_AS_OF,
        **kwargs,  # type: ignore[arg-type]
    )


def test_no_allowlist_at_all_denies() -> None:
    verdict = _evaluate(None, intent=IntentMatch(intent_id="card_balance", confidence=1.0))
    assert verdict.outcome is GateOutcome.DENY  # type: ignore[attr-defined]
    assert verdict.reasons[0].code == policy_gate.NO_ALLOWLIST  # type: ignore[attr-defined]


def test_an_empty_allowlist_refuses_before_anything_else() -> None:
    """Names nobody means admits nobody, and it is decided before the utterance is scored."""
    empty = PackLibrary.from_documents(
        [
            {
                "kind": "allowlist",
                "tenant": "demo-bank",
                "market": "SG",
                "vertical": "retail_banking",
                "locale": "en-SG",
                "intents": [],
                "actions": [],
            }
        ]
    ).allowlist_for("demo-bank", "SG", sample_cases.VERTICAL)
    verdict = _evaluate(empty, intent=IntentMatch(intent_id="card_balance", confidence=1.0))
    assert verdict.outcome is GateOutcome.DENY  # type: ignore[attr-defined]
    assert verdict.reasons[0].code == policy_gate.EMPTY_ALLOWLIST  # type: ignore[attr-defined]


def test_a_match_below_the_floor_denies() -> None:
    verdict = _evaluate(
        _ALLOWLIST, intent=IntentMatch(intent_id="report_lost_card", confidence=0.5)
    )
    assert verdict.outcome is GateOutcome.DENY  # type: ignore[attr-defined]
    assert verdict.reasons[0].code == policy_gate.BELOW_FLOOR  # type: ignore[attr-defined]


def test_an_allowlisted_intent_with_no_action_is_allowed() -> None:
    verdict = _evaluate(_ALLOWLIST, intent=IntentMatch(intent_id="card_balance", confidence=1.0))
    assert verdict.outcome is GateOutcome.ALLOW  # type: ignore[attr-defined]
    assert verdict.citations, "the gate must cite the allowlist entry it applied"


def test_a_consequential_action_composes_to_review_not_allow() -> None:
    """Worst wins: an ALLOW on the intent and a REVIEW on the action compose to REVIEW."""
    verdict = _evaluate(
        _ALLOWLIST,
        intent=IntentMatch(intent_id="report_lost_card", confidence=1.0),
        requested_action="block_card",
        action_spec=SHIPPED_PACKS.action_spec("block_card", sample_cases.VERTICAL),
    )
    assert verdict.outcome is GateOutcome.REVIEW  # type: ignore[attr-defined]
    assert policy_gate.ACTION_CONSEQUENTIAL in {r.code for r in verdict.reasons}  # type: ignore[attr-defined]


def test_an_action_the_intent_may_not_reach_denies() -> None:
    verdict = _evaluate(
        _ALLOWLIST,
        intent=IntentMatch(intent_id="card_balance", confidence=1.0),
        requested_action="raise_chargeback",
        action_spec=SHIPPED_PACKS.action_spec("raise_chargeback", sample_cases.VERTICAL),
    )
    assert verdict.outcome is GateOutcome.DENY  # type: ignore[attr-defined]
    assert policy_gate.ACTION_NOT_FOR_INTENT in {r.code for r in verdict.reasons}  # type: ignore[attr-defined]


def test_another_tenant_s_allowlist_never_applies() -> None:
    verdict = policy_gate.evaluate(
        _ALLOWLIST,
        tenant="rival-bank",
        market="SG",
        intent=IntentMatch(intent_id="card_balance", confidence=1.0),
        as_of=_AS_OF,
    )
    assert verdict.outcome is GateOutcome.DENY
    assert verdict.reasons[0].code == policy_gate.TENANT_MISMATCH


def test_an_empty_reason_list_is_a_denial_not_a_permission() -> None:
    assert policy_gate.worst(()) is GateOutcome.DENY


# --------------------------------------------------------------------------- #
# Pack validity
# --------------------------------------------------------------------------- #
def test_every_shipped_pack_loads_and_cross_references_resolve() -> None:
    SHIPPED_PACKS.check_cross_references()
    assert SHIPPED_PACKS.procedures and SHIPPED_PACKS.disclosures
    assert SHIPPED_PACKS.allowlists and SHIPPED_PACKS.catalogs and SHIPPED_PACKS.cues


def test_an_action_with_no_consequential_flag_is_refused_at_load() -> None:
    """A catalog that forgot to say is not a catalog saying no."""
    with pytest.raises(PackError, match="consequential"):
        PackLibrary.from_documents(
            [
                {
                    "kind": "actions",
                    "catalog_id": "broken-v1",
                    "actions": [{"action_id": "x", "title": "X", "severity": "low"}],
                }
            ]
        )


def test_a_disclosure_triggering_on_a_state_nobody_defines_is_refused_at_load() -> None:
    """A window that can never open is not a disclosure requirement."""
    with pytest.raises(PackError, match="procedure state"):
        PackLibrary.from_documents(
            [
                {
                    "kind": "disclosure",
                    "pack_id": "broken-v1",
                    "market": "SG",
                    "vertical": "retail_banking",
                    "jurisdiction": "MAS",
                    "locale": "en-SG",
                    "disclosures": [
                        {
                            "disclosure_id": "d1",
                            "required_phrase": "we must say this",
                            "trigger_event": "procedure_state:does_not_exist",
                            "severity": "high",
                            "reminder": "say it",
                        }
                    ],
                }
            ]
        )
