"""ONE canonical request per port, shared by the structural and behavioural contract suites.

Parity means the same request through every implementation, so the request needs a single home.
Retyping it per suite is how two "parity" tests end up asserting different things.

Each :class:`PortCase` answers three questions about one port:

* ``invoke``   : what a single canonical call to this port looks like;
* ``answered`` : what it means for the OFFLINE family to have actually answered (a port that
  returns ``None`` and records nothing has not answered, it has merely not raised);
* ``managed_refusal`` : what the MANAGED family must do when called with no cloud reachable.
  Never a silent success: either it refuses because it is unconfigured, or its lazy SDK import
  fails. Both are honest; returning as if the work happened is not.

Adding a port means adding a case here. ``test_port_parity.py`` fails the build if this table
and the port map ever disagree, so the touch list in ``CONTRIBUTING.md`` is enforced rather than
merely written down.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent_eval_kit import EvalReport
from hex_service_kit.identity import IdentityError, Principal, RequestContext
from hex_service_kit.observability import TokenUsage
from speech_lexicon_kit import (
    AudioRef,
    ChannelRole,
    DiarizationRequest,
    SpeakerTurn,
    SpeechSynthesisRequest,
    Transcript,
    TranscriptionRequest,
)

from contact_centre_conversations.domain.kernel import (
    AuditEvent,
    Citation,
    Decision,
    Severity,
)
from contact_centre_conversations.domain.models import (
    ActionCall,
    AssistResult,
    DisclosureReport,
    GateOutcome,
    NextBestStep,
    PolicyVerdict,
    ProcedureProgress,
    RetrievalQuery,
    ScreenOutcome,
    ScreenResult,
    SelfServiceResult,
)
from contact_centre_conversations.ports.voice_engine import (
    CallerUtterance,
    EngineAudio,
    VoiceSessionConfig,
)

from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)

#: The audio every speech-port implementation is handed. A fixture reference, because the
#: offline family replays scripts and no test may depend on a real recording.
CANONICAL_AUDIO = AudioRef(uri=f"fixture://{sample_cases.CLEAN_CONTACT_ID}", media_type="audio/wav")

#: The audit record every audit-port implementation is handed. Already redacted, as the port
#: requires: a raw identifier must never reach a WORM record. Tagged with the MODE, because a
#: record whose mode is unknown cannot be counted towards either mode's promotion evidence.
CANONICAL_EVENT = AuditEvent(
    action="agent_assist.turn",
    actor=sample_cases.ACTOR,
    decision=Decision.ESCALATED,
    severity=Severity.HIGH,
    redacted_summary="contact-sg-0001: state=block_card missed=1",
    citations=(Citation(source_id="pack:sg-card-dispute-v1#block_card", title="Block the card"),),
    mode="agent_assist",
    contact_id=sample_cases.CLEAN_CONTACT_ID,
    tenant=sample_cases.TENANT,
)

_MISSED = DisclosureReport(
    pack_id="sg-retail-disclosures-v1",
    market=sample_cases.MARKET,
    as_of=_AS_OF,
    statuses=(),
)

#: The escalated agent-assist result every review-router implementation is handed (R8's payload).
CANONICAL_RESULT = AssistResult(
    contact=sample_cases.AGENT_CONTACT,
    transcript=Transcript(
        transcript_id=sample_cases.CLEAN_CONTACT_ID,
        locale=sample_cases.LOCALE,
        turns=(
            SpeakerTurn(
                index=0, speaker_id="agent-1", role=ChannelRole.AGENT, text="Thank you for calling."
            ),
        ),
    ),
    screen=ScreenResult(outcome=ScreenOutcome.CLEAN, turn_index=0),
    progress=ProcedureProgress(
        pack_id="sg-card-dispute-v1",
        state_id="block_card",
        completed_state_ids=("greeting",),
        satisfied_evidence=("greeting_given",),
        missing_evidence=("card_blocked",),
        as_of=_AS_OF,
    ),
    next_step=NextBestStep(
        state_id="block_card",
        instruction="Block the card and tell the caller you have done it.",
        rationale="card_blocked is still missing",
        citations=(Citation(source_id="pack:sg-card-dispute-v1#block_card", title="Block"),),
    ),
    disclosures=_MISSED,
    requires_human_review=True,
)

#: The self-service result, so the review payload builder is exercised for BOTH modes: they
#: escalate for different reasons and a router that only ever saw one would not be tested.
CANONICAL_SELF_SERVICE_RESULT = SelfServiceResult(
    contact=sample_cases.CUSTOMER_CONTACT,
    transcript=Transcript(
        transcript_id=sample_cases.SELF_SERVICE_CONTACT_ID,
        locale=sample_cases.LOCALE,
        turns=(
            SpeakerTurn(
                index=0, speaker_id="customer", role=ChannelRole.CUSTOMER, text="Block my card."
            ),
        ),
    ),
    screen=ScreenResult(outcome=ScreenOutcome.CLEAN, turn_index=0),
    verdict=PolicyVerdict(
        outcome=GateOutcome.REVIEW,
        as_of=_AS_OF,
        tenant=sample_cases.TENANT,
        market=sample_cases.MARKET,
        intent_id="report_lost_card",
        action_id="block_card",
    ),
    disclosures=_MISSED,
    requires_human_review=True,
)

#: The inbound transport context every identity implementation is handed.
CANONICAL_CONTEXT = RequestContext(headers={"x-dev-persona": "auditor"})

#: The retrieval query every knowledge-base implementation is handed.
CANONICAL_QUERY = RetrievalQuery(
    text="what is my card balance",
    filters={"market": sample_cases.MARKET, "locale": sample_cases.LOCALE},
)

#: The action every tool-catalog implementation is asked to describe and (offline) execute. It is
#: deliberately a NON-consequential one: the consequential path is proved by counting the calls
#: that did NOT happen, in ``tests/unit/test_maker_checker.py``.
CANONICAL_ACTION = ActionCall(
    action_id="read_card_balance",
    contact_id=sample_cases.SELF_SERVICE_CONTACT_ID,
    tenant=sample_cases.TENANT,
    vertical=sample_cases.VERTICAL,
    parameters={"card_last4": "4321"},
)


@dataclass(frozen=True, slots=True)
class PortCase:
    """One port's canonical call plus the two verdicts the parity suites need."""

    invoke: Callable[[Any], Any]
    answered: Callable[[Any, Any], bool]
    managed_refusal: tuple[type[BaseException], ...]
    detail: str


def _audit_invoke(adapter: Any) -> Any:
    return adapter.record(CANONICAL_EVENT)


def _audit_answered(adapter: Any, _result: Any) -> bool:
    stored = adapter.log.read_all()
    return bool(stored) and stored[-1]["actor"] == sample_cases.ACTOR and adapter.verify().ok


def _identity_invoke(adapter: Any) -> Any:
    return adapter.resolve(CANONICAL_CONTEXT)


def _identity_answered(_adapter: Any, result: Any) -> bool:
    return isinstance(result, Principal) and bool(result.actor)


def _review_invoke(adapter: Any) -> Any:
    return adapter.route(CANONICAL_RESULT, maker=sample_cases.ACTOR, tenant=sample_cases.TENANT)


def _review_answered(adapter: Any, result: Any) -> bool:
    return bool(result) and len(adapter.outbox.pending()) == 1


def _retrieval_invoke(adapter: Any) -> Any:
    return adapter.retrieve(CANONICAL_QUERY)


def _retrieval_answered(_adapter: Any, result: Any) -> bool:
    return bool(result) and all(passage.citation.source_id for passage in result)


def _party_records_invoke(adapter: Any) -> Any:
    # The party in the ownership fixture, asking about the record that fixture says is theirs.
    return adapter.owns(
        party_ref="party-sg-0001",
        tenant=sample_cases.TENANT,
        parameter="card_last4",
        value="4321",
    )


def _party_records_answered(adapter: Any, result: Any) -> bool:
    # Answering is not enough: an adapter that said True to everything would satisfy a
    # yes-only check while being exactly the defect ownership exists to catch. So the canonical
    # call also asks about a record the fixture gives to somebody else.
    others = adapter.owns(
        party_ref="party-sg-0001",
        tenant=sample_cases.TENANT,
        parameter="card_last4",
        value="9876",
    )
    return result is True and others is False


def _generation_invoke(adapter: Any) -> Any:
    return adapter.draft("what is my card balance", _passages())


def _generation_answered(_adapter: Any, result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("text")) and bool(result.get("passage_ids"))


def _guardrail_invoke(adapter: Any) -> Any:
    return adapter.screen("what is my card balance", turn_index=0)


def _guardrail_answered(_adapter: Any, result: Any) -> bool:
    return isinstance(result, ScreenResult) and result.outcome is ScreenOutcome.CLEAN


def _tool_invoke(adapter: Any) -> Any:
    return adapter.execute(CANONICAL_ACTION)


def _tool_answered(adapter: Any, result: Any) -> bool:
    return bool(result.executed) and len(adapter.calls) == 1


def _store_invoke(adapter: Any) -> Any:
    adapter.create(sample_cases.AGENT_CONTACT)
    adapter.append_turn(
        sample_cases.AGENT_CONTACT.contact_id,
        SpeakerTurn(index=0, speaker_id="agent-1", role=ChannelRole.AGENT, text="Hello."),
        tenant=sample_cases.TENANT,
    )
    return adapter.turns(sample_cases.AGENT_CONTACT.contact_id, tenant=sample_cases.TENANT)


def _store_answered(_adapter: Any, result: Any) -> bool:
    return len(result) == 1 and result[0].text == "Hello."


def _stt_invoke(adapter: Any) -> Any:
    return adapter.transcribe(
        TranscriptionRequest(request_id="r1", audio=CANONICAL_AUDIO, locale=sample_cases.LOCALE)
    )


def _stt_answered(_adapter: Any, result: Any) -> bool:
    return bool(result.transcript.turns)


def _tts_invoke(adapter: Any) -> Any:
    return adapter.synthesize(
        SpeechSynthesisRequest(request_id="r2", text="Hello.", locale=sample_cases.LOCALE)
    )


def _tts_answered(_adapter: Any, result: Any) -> bool:
    return bool(result.audio.uri)


def _diarization_invoke(adapter: Any) -> Any:
    return adapter.diarize(DiarizationRequest(request_id="r3", audio=CANONICAL_AUDIO))


def _diarization_answered(_adapter: Any, result: Any) -> bool:
    return bool(result.segments)


def _voice_engine_invoke(adapter: Any) -> Any:
    """One tiny call: connect, hear one caller utterance, voice one line, hang up."""

    async def drive() -> dict[str, Any]:
        session = await adapter.connect(VoiceSessionConfig(contact=sample_cases.CUSTOMER_CONTACT))
        await session.send_caller_text("what is my card balance")
        events = session.events()
        first = await anext(events)
        spoken = await session.say("Thank you for calling.")
        await session.close()
        return {"first": first, "spoken": spoken}

    return asyncio.run(drive())


def _voice_engine_answered(_adapter: Any, result: Any) -> bool:
    return (
        isinstance(result["first"], CallerUtterance)
        and bool(result["first"].text)
        and isinstance(result["spoken"], EngineAudio)
        and len(result["spoken"].pcm) > 0
    )


def _channel_invoke(adapter: Any) -> Any:
    contact = sample_cases.AGENT_CONTACT
    adapter.open(contact)
    adapter.send(contact, "Thank you for calling.")
    return list(adapter.turns(contact))


def _channel_answered(adapter: Any, result: Any) -> bool:
    return bool(result) and len(adapter.sent) == 1


def _passages() -> list[Any]:
    from contact_centre_conversations.domain.models import RetrievedPassage

    return [
        RetrievedPassage(
            text="An agent may state the current card balance after the caller is verified.",
            citation=Citation(source_id="kb-sg-001", title="Card balance enquiries"),
            score=1.0,
        )
    ]


def _tracer_invoke(adapter: Any) -> Any:
    with adapter.span("canonical.unit", action="canonical"):
        adapter.record_token_usage(TokenUsage(input_tokens=7, output_tokens=2), "canonical-model")
    return True


def _tracer_answered(adapter: Any, result: Any) -> bool:
    return bool(result)


def _evaluation_invoke(adapter: Any) -> Any:
    return adapter.evaluate("eval/datasets/canonical.jsonl")


def _evaluation_answered(adapter: Any, result: Any) -> bool:
    return isinstance(result, EvalReport) and result.dataset.endswith("canonical.jsonl")


CANONICAL_CALLS: dict[str, PortCase] = {
    "audit": PortCase(
        invoke=_audit_invoke,
        answered=_audit_answered,
        # The lazy `google.cloud` import is the first thing the managed sink does.
        managed_refusal=(ImportError,),
        detail="write one already-redacted WORM record",
    ),
    "identity": PortCase(
        invoke=_identity_invoke,
        answered=_identity_answered,
        # No IAP assertion header offline, so the managed adapter refuses before importing.
        managed_refusal=(IdentityError,),
        detail="resolve a verified principal from transport context",
    ),
    "review_router": PortCase(
        invoke=_review_invoke,
        answered=_review_answered,
        # Rule R8: with no console configured the managed router must refuse, not swallow.
        managed_refusal=(RuntimeError,),
        detail="route one escalated result to human review",
    ),
    "party_records": PortCase(
        invoke=_party_records_invoke,
        answered=_party_records_answered,
        # Unconfigured base URL: the platform client refuses rather than defaulting to a host.
        managed_refusal=(RuntimeError,),
        detail="answer whether one party owns the record a parameter names",
    ),
    "retrieval": PortCase(
        invoke=_retrieval_invoke,
        answered=_retrieval_answered,
        # Unconfigured base URL: the platform client refuses rather than defaulting to a host.
        managed_refusal=(RuntimeError,),
        detail="return ranked, cited passages for one query",
    ),
    "generation": PortCase(
        invoke=_generation_invoke,
        answered=_generation_answered,
        managed_refusal=(ImportError,),
        detail="draft one grounded reply from retrieved passages",
    ),
    "guardrail": PortCase(
        invoke=_guardrail_invoke,
        answered=_guardrail_answered,
        managed_refusal=(RuntimeError,),
        detail="screen one already-redacted turn for injection",
    ),
    "tool_catalog": PortCase(
        invoke=_tool_invoke,
        answered=_tool_answered,
        managed_refusal=(RuntimeError,),
        detail="execute one non-consequential, validated action",
    ),
    "contact_store": PortCase(
        invoke=_store_invoke,
        answered=_store_answered,
        managed_refusal=(ImportError,),
        detail="persist and read back one redacted turn, tenant-scoped",
    ),
    "speech_to_text": PortCase(
        invoke=_stt_invoke,
        answered=_stt_answered,
        managed_refusal=(ImportError,),
        detail="transcribe one contact into speaker turns",
    ),
    "text_to_speech": PortCase(
        invoke=_tts_invoke,
        answered=_tts_answered,
        managed_refusal=(ImportError,),
        detail="synthesise one utterance",
    ),
    "diarization": PortCase(
        invoke=_diarization_invoke,
        answered=_diarization_answered,
        managed_refusal=(ImportError,),
        detail="attribute one contact's audio to speakers",
    ),
    "conversation_channel": PortCase(
        invoke=_channel_invoke,
        answered=_channel_answered,
        managed_refusal=(ImportError,),
        detail="open a session, read inbound turns, deliver one message",
    ),
    "voice_engine": PortCase(
        invoke=_voice_engine_invoke,
        answered=_voice_engine_answered,
        # The lazy speech SDK import is the first thing the managed cascade engine does.
        managed_refusal=(ImportError,),
        detail="open a realtime session, hear one utterance, voice one line",
    ),
    "tracer": PortCase(
        invoke=_tracer_invoke,
        answered=_tracer_answered,
        # NOTHING. Tracing is not essential to correctness, so the managed adapter must not refuse
        # offline either: with no SDK installed it degrades to a no-op and the traced body still
        # runs. An adapter that raised here would take a request down over a diagnostic.
        managed_refusal=(),
        detail="open one span and report the cost of a model call",
    ),
    "evaluation": PortCase(
        invoke=_evaluation_invoke,
        answered=_evaluation_answered,
        # The managed gate reaches model-quality-gate over HTTP, which is unreachable offline.
        managed_refusal=(Exception,),
        detail="score one golden dataset through the promotion authority",
    ),
}
