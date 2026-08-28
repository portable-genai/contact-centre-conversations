"""The per-call orchestrator: engine events in, deterministic decisions out, speech both ways.

One instance per answered call. It owns the things a phone call adds to the existing
self-service pipeline and NOTHING the pipeline already owns:

* it feeds every finalized caller utterance (spoken, keyed as DTMF, or typed in the paired chat
  session) through :meth:`~..domain.self_service.SelfServiceService.handle`, exactly as the
  chat API route does, so the gate, the disclosures, the audit record and rule R8 run
  identically whichever surface the customer used;
* it derives its speaking duties from what the bound engine DECLARES
  (:func:`~..ports.voice_engine.declared_speech_authorship`): behind a transcribe-only engine
  it voices only what the pipeline authored, and unsolicited engine audio is a defect that
  ends the call safely; behind an authoring engine it shadow-gates the live transcript and
  holds the kill switch;
* it carries the conversation ACROSS channels: the stored redacted transcript seeds the engine
  at connect, and a poll of the contact store forwards chat turns that arrive mid-call, so
  "switch between chat and voice" is a property of the contact, not of a session;
* it survives engine reconnects (a Live ``goAway``) behind the newest resumption handle while
  the gateway keeps the telephone leg alive.

It talks to the telephony side only through :class:`CallTransport`, so the whole orchestration
is unit-testable with a scripted engine and a recording transport, no socket in the room.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from pii_kit import redact
from speech_lexicon_kit import ChannelRole, SpeakerTurn

from ..config import Settings
from ..domain.kernel import utcnow
from ..domain.models import ContactRef, SelfServiceResult, TurnSubmission
from ..domain.pii import PII_PATTERNS
from ..domain.self_service import SelfServiceService, SessionState
from ..ports.contact_store import ContactStorePort
from ..ports.tool_catalog import ToolCatalogPort
from ..ports.voice_engine import (
    TRANSCRIBES_ONLY,
    CallerUtterance,
    EngineAudio,
    EngineClosed,
    EngineInterrupted,
    EngineResumptionHandle,
    EngineToolCall,
    EngineUtterance,
    VoiceEnginePort,
    VoiceEngineSession,
    VoiceSessionConfig,
    VoiceToolSpec,
    declared_speech_authorship,
)

_log = logging.getLogger(__name__)

#: The actor recorded on every audit row this surface produces. A machine surface names itself.
VOICE_ACTOR = "voice-gateway"

#: The speaker id every turn THIS session stores carries. The cross-channel poll skips it, so a
#: caller's own spoken or keyed turn is never read back out of the store and forwarded to the
#: engine as though the customer had typed it in chat.
CALLER_SPEAKER = "caller"

#: Deterministic service prose. Fixed lines, not model output, so every caller in a given
#: deployment hears the same words in the same situation. A production deployment localises
#: these through its reviewed packs; the constants keep the reference gateway honest offline.
FALLBACK_LINE = "I am sorry, I cannot help with that request here. Let me get a person to help."
REFUSAL_LINE = "I am sorry, I cannot help with that request on this line."
HANDOFF_LINE = "I am connecting you to a member of our team now. Please stay on the line."
TROUBLE_LINE = "I am having technical difficulty. Please stay on the line or call back shortly."


class CallTransport(Protocol):
    """What the orchestrator may do to the telephone leg. The gateway implements this."""

    def play_pcm(self, pcm: bytes, sample_rate_hz: int) -> None:
        """Queue audio for paced playout to the caller."""
        ...

    def flush_playout(self) -> None:
        """Drop everything queued but not yet played (barge-in, and the kill switch)."""
        ...

    def request_transfer(self, reason: str) -> None:
        """Ask the telephony layer to REFER the caller to the human queue."""
        ...

    def hangup(self) -> None:
        """End the call from this side."""
        ...


class VoiceCallSession:
    """Orchestrate one call between the transport, the engine and the deterministic pipeline."""

    def __init__(
        self,
        *,
        settings: Settings,
        contact: ContactRef,
        engine: VoiceEnginePort,
        service: SelfServiceService,
        store: ContactStorePort,
        tools: ToolCatalogPort,
        transport: CallTransport,
    ) -> None:
        self._settings = settings
        self._contact = contact
        self._engine = engine
        self._service = service
        self._store = store
        self._tools = tools
        self._transport = transport
        self._authorship = declared_speech_authorship(engine)
        self._state = SessionState()
        self._session: VoiceEngineSession | None = None
        self._resume_handle = ""
        self._store_seen = 0
        self._turn_index = 0
        self._last_utterance = ""
        self._stopping = False
        self._chat_poll: asyncio.Task[None] | None = None
        #: Set once the kill switch has fired. An authoring engine keeps streaming the audio of
        #: the refused turn (it cannot be force-silenced), so every EngineAudio after the switch
        #: must be DROPPED rather than played; the switch always ends in a transfer, so this
        #: stays set for the rest of the call.
        self._engine_muted = False

    @property
    def transcribes_only(self) -> bool:
        return self._authorship == TRANSCRIBES_ONLY

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        """Connect the engine (seeded with the stored transcript) and greet the caller."""
        context = tuple(await asyncio.to_thread(self._stored_turns))
        self._session = await self._engine.connect(self._config(context))
        self._store_seen = len(context)
        greeting = self._settings.voice.greeting
        if greeting:
            await self._say(greeting)
        if self._settings.voice.chat_poll_ms > 0:
            self._chat_poll = asyncio.create_task(self._poll_chat_turns())

    async def run(self) -> None:
        """Consume engine events until the engine closes or the call is stopped."""
        while not self._stopping:
            session = self._require_session()
            reconnect = False
            async for event in session.events():
                if isinstance(event, EngineClosed):
                    reconnect = await self._on_closed(event)
                    break
                await self._on_event(event)
            if not reconnect:
                break

    async def stop(self) -> None:
        """End the session from the telephony side (BYE received, or gateway shutdown)."""
        self._stopping = True
        if self._chat_poll is not None:
            self._chat_poll.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._chat_poll
        if self._session is not None:
            handle = await self._session.close()
            self._resume_handle = handle or self._resume_handle

    # ------------------------------------------------------------------ telephony inbound
    async def on_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        if self._session is not None and not self._stopping:
            await self._session.send_caller_audio(pcm, sample_rate_hz=sample_rate_hz)

    async def on_digits(self, dialled: str) -> None:
        """A completed DTMF string is an utterance: same pipeline, same audit, same gate."""
        await self._handle_utterance(dialled)

    # ------------------------------------------------------------------ engine events
    async def _on_event(self, event: object) -> None:
        if isinstance(event, CallerUtterance):
            if event.final and event.text.strip():
                await self._handle_utterance(event.text.strip())
        elif isinstance(event, EngineAudio):
            if self._engine_muted:
                # The kill switch has fired; the model is still streaming the refused turn's
                # audio, which must never reach the caller after the refusal.
                return
            if self.transcribes_only:
                # A transcribe-only engine has no voice. Audio from it is a defect, and the
                # safe end to a defective call is a person, not a coin toss.
                await self._kill_switch(handoff=False)
            else:
                self._transport.play_pcm(event.pcm, event.sample_rate_hz)
        elif isinstance(event, EngineUtterance):
            # The authoring engine's own words. The per-turn shadow gate runs on the CALLER
            # utterance that provoked them; the utterance text is kept for the audit trail via
            # the store-backed transcript, so nothing extra is decided here.
            pass
        elif isinstance(event, EngineToolCall):
            await self._handle_tool_call(event)
        elif isinstance(event, EngineInterrupted):
            self._transport.flush_playout()
        elif isinstance(event, EngineResumptionHandle):
            self._resume_handle = event.handle

    async def _on_closed(self, event: EngineClosed) -> bool:
        """Reconnect behind the newest handle when the engine says so; otherwise end safely.

        A FAILURE owes the caller an apology and a person; a graceful end owes nothing, and
        the gateway ends the call once the event stream is done.
        """
        if self._stopping:
            return False
        if event.resumable:
            context = tuple(await asyncio.to_thread(self._stored_turns))
            self._session = await self._engine.connect(self._config(context))
            return True
        if event.failure:
            await self._say(TROUBLE_LINE)
            self._transport.request_transfer("engine-failed")
        return False

    # ------------------------------------------------------------------ the turn
    async def _handle_utterance(self, text: str) -> None:
        self._last_utterance = text
        result = await self._run_pipeline(text)
        for status in result.disclosures.due:
            if status.reminder_text:
                await self._say(status.reminder_text)
        if self.transcribes_only:
            await self._speak_reply(result)
        else:
            await self._shadow_gate(result)
        if result.handoff is not None:
            await self._say(HANDOFF_LINE)
            self._transport.request_transfer(result.handoff.trigger.value)

    async def _run_pipeline(
        self, text: str, *, requested_action: str = "", parameters: dict[str, str] | None = None
    ) -> SelfServiceResult:
        submission = TurnSubmission(
            contact=self._contact,
            index=self._next_index(),
            speaker_id=CALLER_SPEAKER,
            role=ChannelRole.CUSTOMER,
            text=text,
        )
        # ``handle`` is synchronous and touches the store, the audit sink and (on escalation)
        # the review console. Off the event loop, so the audio path never waits on a decision.
        return await asyncio.to_thread(
            self._service.handle,
            submission,
            actor=VOICE_ACTOR,
            as_of=utcnow(),
            session=self._state,
            requested_action=requested_action,
            parameters=parameters or {},
        )

    async def _speak_reply(self, result: SelfServiceResult) -> None:
        if result.handoff is not None:
            return  # the handoff line is spoken by the caller of this method
        if result.suggestion is not None and result.suggestion.text:
            await self._say(result.suggestion.text)
        else:
            await self._say(REFUSAL_LINE)

    async def _shadow_gate(self, result: SelfServiceResult) -> None:
        """The authoring engine already spoke or is speaking. Refuse AFTER the fact is still
        refusing: flush what has not played yet and put a deterministic sentence in its place."""
        if not result.screen.safe_for_model or not result.verdict.allowed:
            await self._kill_switch(handoff=result.handoff is not None)

    async def _kill_switch(self, *, handoff: bool) -> None:
        session = self._require_session()
        # Mute BEFORE flushing: an authoring engine cannot be force-silenced, so its in-flight
        # frames keep arriving; from here they are dropped rather than queued behind the
        # fallback line the caller is about to hear.
        self._engine_muted = True
        self._transport.flush_playout()
        await session.interrupt()
        await self._say(FALLBACK_LINE)
        if not handoff:
            self._transport.request_transfer("kill-switch")

    async def _handle_tool_call(self, call: EngineToolCall) -> None:
        """The engine may REQUEST an action; the deterministic action gate decides it."""
        result = await self._run_pipeline(
            self._last_utterance or f"[tool request: {call.action_id}]",
            requested_action=call.action_id,
            parameters=dict(call.parameters),
        )
        outcome = result.action
        payload: dict[str, object] = {
            "executed": bool(outcome.executed) if outcome else False,
            "detail": outcome.detail if outcome else "the action gate returned no outcome",
            "reference": outcome.reference if outcome else "",
            "requires_human_review": result.requires_human_review,
        }
        session = self._require_session()
        await session.send_tool_result(call.call_id, payload)
        if result.handoff is not None:
            await self._say(HANDOFF_LINE)
            self._transport.request_transfer(result.handoff.trigger.value)

    # ------------------------------------------------------------------ cross-channel
    async def _poll_chat_turns(self) -> None:
        """Forward chat turns that arrive MID-CALL into the live session as context.

        The chat route has already gated, audited and answered them in chat; re-deciding them
        here would double every consequence. What the voice side owes is continuity: the engine
        should know what the customer just typed. The store holds REDACTED text, and it is
        redacted again on the way out, so a pattern added after storage still cannot leak.

        This task is the sole writer of the cursor and the sole reader of the store here, so
        there is no race with the turn path: the session's OWN turns are skipped by speaker id
        (:data:`CALLER_SPEAKER`) rather than by trying to keep two cursors in step. A per-tick
        failure is logged and swallowed so one bad send cannot end forwarding for the call.
        """
        interval = self._settings.voice.chat_poll_ms / 1000
        while not self._stopping:
            await asyncio.sleep(interval)
            try:
                turns = await asyncio.to_thread(self._stored_turns)
                fresh = turns[self._store_seen :]
                self._store_seen = len(turns)
                session = self._session
                if session is None:
                    continue
                for turn in fresh:
                    if (
                        turn.role is ChannelRole.CUSTOMER
                        and turn.speaker_id != CALLER_SPEAKER
                        and turn.text.strip()
                    ):
                        text = redact(turn.text, PII_PATTERNS)
                        await session.send_caller_text(f"[via chat] {text}")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - continuity is best-effort; never kill the task
                _log.warning("chat-to-voice forwarding tick failed", exc_info=True)

    def _stored_turns(self) -> list[SpeakerTurn]:
        return list(self._store.turns(self._contact.contact_id, tenant=self._contact.tenant))

    # ------------------------------------------------------------------ helpers
    async def _say(self, text: str) -> None:
        session = self._require_session()
        audio = await session.say(text)
        self._transport.play_pcm(audio.pcm, audio.sample_rate_hz)

    def _config(self, context: tuple[SpeakerTurn, ...]) -> VoiceSessionConfig:
        return VoiceSessionConfig(
            contact=self._contact,
            system_prompt=self._settings.voice.system_prompt,
            context_turns=context,
            tools=self._tool_specs(),
            resume_handle=self._resume_handle,
        )

    def _tool_specs(self) -> tuple[VoiceToolSpec, ...]:
        """The allowlisted actions, as declarations an authoring engine may REQUEST.

        Built from the same reviewed allowlist pack the gate enforces, so the engine can never
        even name an action the gate would not recognise. Requesting still grants nothing.
        """
        allowlist = self._settings.packs.allowlist_for(
            self._contact.tenant, self._contact.market, self._contact.vertical
        )
        if allowlist is None:
            return ()
        specs = []
        for action_id in allowlist.allowed_actions:
            spec = self._tools.describe(action_id, self._contact.vertical)
            if spec is None:
                continue
            specs.append(
                VoiceToolSpec(
                    action_id=spec.action_id,
                    description=spec.title,
                    parameter_names=tuple(p.name for p in spec.parameters),
                )
            )
        return tuple(specs)

    def _next_index(self) -> int:
        index = self._turn_index
        self._turn_index += 1
        return index

    def _require_session(self) -> VoiceEngineSession:
        if self._session is None:
            raise RuntimeError("the engine session is not connected; call start() first")
        return self._session
