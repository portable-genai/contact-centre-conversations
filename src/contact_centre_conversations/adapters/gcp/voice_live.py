"""Managed GEMINI LIVE voice engine: a native audio-to-audio model session per call.

This is the engine that makes the call sound like a person: server-side voice activity
detection, natural barge-in, and speech the model authors in its own voice. That last property
is exactly why it declares :data:`AUTHORS_SPEECH`: the words a customer hears are the model's,
so the session orchestrator runs the deterministic pipeline as a SHADOW GATE over the live
transcripts and holds a kill switch this adapter cannot be trusted to hold itself.

Two invariants this engine RELAXES, stated here because hiding them would be worse:

* Raw caller audio reaches the model before any redaction can run. Text can be redacted before
  a model sees it; a live audio stream cannot. A deployment that cannot accept that binds the
  cascade engine instead, which is the shipped default.
* The Vertex AI Live endpoint serves US and EU regions only today, so this engine runs OUTSIDE
  the deployment's pinned region. ``voice.live_region`` names where, loudly, in configuration.

Tool calls the model requests are surfaced as events and answered only after the deterministic
action gate has decided; deterministic prose (disclosures, refusals, the kill switch fallback)
is voiced by :func:`synthesize_pcm`, never by asking the model to please read something out.

The SDK import is LAZY, inside :meth:`connect`, and an unconfigured or SDK-free environment
REFUSES there rather than answering a phone call with silence.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from ...config import Settings
from ...ports.voice_engine import (
    AUTHORS_SPEECH,
    CallerUtterance,
    EngineAudio,
    EngineClosed,
    EngineInterrupted,
    EngineResumptionHandle,
    EngineToolCall,
    EngineUtterance,
    VoiceEvent,
    VoiceSessionConfig,
)
from ._voice_synth import SYNTH_RATE_HZ, synthesize_pcm

#: The rates the Live API contract fixes: 16 kHz PCM in, 24 kHz PCM out.
_INPUT_RATE_HZ = 16_000
_OUTPUT_RATE_HZ = 24_000

#: How many context-window tokens trigger sliding compression. Compression is what lifts the
#: session-length ceiling AND what bounds the per-turn context re-billing (the Live API bills
#: the whole context every turn).
_COMPRESSION_TRIGGER_TOKENS = 16_000


class GeminiLiveVoiceEngine:
    """Open one Live API session per call, over Vertex AI or the Gemini API by configuration."""

    speech_authorship = AUTHORS_SPEECH

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def connect(self, config: VoiceSessionConfig) -> GeminiLiveVoiceSession:
        # Lazy: the offline profiles must import this module with no SDK present.
        from google import genai  # noqa: PLC0415
        from google.genai import types  # noqa: PLC0415

        voice = self._settings.voice
        if voice.live_endpoint == "vertex":
            client = genai.Client(vertexai=True, location=voice.live_region)
        else:
            # The Gemini API endpoint: global, keyed, no residency claim. The SDK reads its own
            # credential; this adapter never touches a secret value.
            client = genai.Client()
        live_config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=config.system_prompt or None,
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            session_resumption=types.SessionResumptionConfig(handle=config.resume_handle or None),
            context_window_compression=types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
                trigger_tokens=_COMPRESSION_TRIGGER_TOKENS,
            ),
            tools=_tool_declarations(types, config),
        )
        connection = client.aio.live.connect(model=voice.live_model, config=live_config)
        session = await connection.__aenter__()
        live = GeminiLiveVoiceSession(self._settings, types, connection, session)
        await live.seed_context(config)
        return live


class GeminiLiveVoiceSession:
    """One live model session: audio both ways, transcripts both ways, tools by request."""

    def __init__(self, settings: Settings, types: Any, connection: Any, session: Any) -> None:
        self._settings = settings
        self._types = types
        self._connection = connection
        self._session = session
        self._handle = ""
        self._closed = False
        #: Input transcription arrives as incremental text DELTAS, with the finished flag only
        #: on the last fragment of a segment. The whole utterance, not its trailing fragment,
        #: is what the deterministic pipeline and the shadow gate must judge, so fragments
        #: accumulate here until finished.
        self._input_buffer = ""

    async def seed_context(self, config: VoiceSessionConfig) -> None:
        """Seed the stored (already redacted) transcript so chat context carries into voice.

        Initial seeding is the one supported use of ``send_client_content`` on current Live
        models; live input goes through the realtime channel instead.
        """
        if not config.context_turns:
            return
        types = self._types
        turns = [
            types.Content(
                role="user" if turn.role.value == "customer" else "model",
                parts=[types.Part(text=turn.text)],
            )
            for turn in config.context_turns
            if turn.text.strip()
        ]
        if turns:
            await self._session.send_client_content(turns=turns, turn_complete=False)

    # ------------------------------------------------------------------ port surface
    async def send_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        if sample_rate_hz != _INPUT_RATE_HZ:
            raise ValueError(
                f"the live engine expects {_INPUT_RATE_HZ} Hz caller audio, "
                f"got {sample_rate_hz} Hz; resample at the gateway boundary"
            )
        blob = self._types.Blob(data=pcm, mime_type=f"audio/pcm;rate={_INPUT_RATE_HZ}")
        await self._session.send_realtime_input(audio=blob)

    async def send_caller_text(self, text: str) -> None:
        await self._session.send_realtime_input(text=text)

    async def send_tool_result(self, call_id: str, result: Mapping[str, object]) -> None:
        response = self._types.FunctionResponse(id=call_id, name="", response=dict(result))
        await self._session.send_tool_response(function_responses=[response])

    async def say(self, text: str) -> EngineAudio:
        """Deterministic prose is synthesized, never entrusted to the model's paraphrase."""
        pcm = await asyncio.to_thread(synthesize_pcm, text, self._settings)
        return EngineAudio(pcm=pcm, sample_rate_hz=SYNTH_RATE_HZ)

    async def interrupt(self) -> None:
        """The model cannot be force-silenced mid-utterance; the kill switch is the gateway
        flushing playout. This method exists so the orchestrator's call sites are uniform."""

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while not self._closed:
            try:
                async for message in self._session.receive():
                    for event in self._map(message):
                        yield event
                        if isinstance(event, EngineClosed):
                            return
            except Exception as exc:  # noqa: BLE001 - a dead session must surface as an event
                if not self._closed:
                    yield EngineClosed(
                        reason=f"live session failed: {type(exc).__name__}", failure=True
                    )
                return

    async def close(self) -> str:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self._connection.__aexit__(None, None, None)
        return self._handle

    # ------------------------------------------------------------------ message mapping
    def _map(self, message: Any) -> list[VoiceEvent]:
        events: list[VoiceEvent] = []
        update = getattr(message, "session_resumption_update", None)
        if update is not None and getattr(update, "resumable", False):
            self._handle = str(getattr(update, "new_handle", "") or "")
            if self._handle:
                events.append(EngineResumptionHandle(handle=self._handle))
        if getattr(message, "go_away", None) is not None:
            events.append(EngineClosed(reason="go-away", resumable=True))
            return events
        tool_call = getattr(message, "tool_call", None)
        if tool_call is not None:
            for call in getattr(tool_call, "function_calls", []) or []:
                events.append(
                    EngineToolCall(
                        call_id=str(getattr(call, "id", "") or ""),
                        action_id=str(getattr(call, "name", "") or ""),
                        parameters={
                            str(k): str(v) for k, v in (getattr(call, "args", None) or {}).items()
                        },
                    )
                )
        content = getattr(message, "server_content", None)
        if content is not None:
            if getattr(content, "interrupted", False):
                events.append(EngineInterrupted())
            transcription = getattr(content, "input_transcription", None)
            if transcription is not None:
                fragment = str(getattr(transcription, "text", "") or "")
                self._input_buffer += fragment
                finished = bool(getattr(transcription, "finished", False))
                if finished and self._input_buffer.strip():
                    events.append(CallerUtterance(text=self._input_buffer.strip(), final=True))
                    self._input_buffer = ""
                elif fragment:
                    # A partial for observability; the pipeline ignores non-final utterances,
                    # so this never reaches the gate on a fragment.
                    events.append(CallerUtterance(text=fragment, final=False))
            # A model turn completing without a trailing finished-flagged transcription still
            # closes the segment: flush whatever accumulated so the gate is never left inert.
            if getattr(content, "turn_complete", False) and self._input_buffer.strip():
                events.append(CallerUtterance(text=self._input_buffer.strip(), final=True))
                self._input_buffer = ""
            out = getattr(content, "output_transcription", None)
            spoken = "" if out is None else str(getattr(out, "text", "") or "")
            if spoken:
                events.append(EngineUtterance(text=spoken))
        data = getattr(message, "data", None)
        if data:
            events.append(EngineAudio(pcm=bytes(data), sample_rate_hz=_OUTPUT_RATE_HZ))
        return events


def _tool_declarations(types: Any, config: VoiceSessionConfig) -> list[Any] | None:
    """The allowlisted actions, declared to the model as requestable functions.

    Declaring a function grants nothing: execution stays behind the deterministic action gate,
    and a call for anything not declared here never even reaches it.
    """
    if not config.tools:
        return None
    declarations = [
        types.FunctionDeclaration(
            name=tool.action_id,
            description=tool.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    name: types.Schema(type=types.Type.STRING) for name in tool.parameter_names
                },
            ),
        )
        for tool in config.tools
    ]
    return [types.Tool(function_declarations=declarations)]
