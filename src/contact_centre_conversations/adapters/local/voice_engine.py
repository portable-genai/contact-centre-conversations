"""Local VoiceEnginePort: a deterministic offline engine for the gate, tests and the demo.

The managed engines stream real speech. This one replays the SAME scripted contact streams the
channel and speech adapters replay (``{streams_path}/<contact_id>.jsonl``), and echoes any text
pushed into the session back as a finalized caller utterance, so the session orchestrator runs
its full loop (turn, gate, reply, disclosure, handoff) with no SDK, no network and no audio.

It declares :data:`~....ports.voice_engine.TRANSCRIBES_ONLY`: the deterministic pipeline authors
every reply, and :meth:`say` returns a deterministic pseudo-audio frame whose LENGTH encodes the
text length, so a test can assert that something was voiced, and how much, without a codec.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from ...config import Settings
from ...ports.voice_engine import (
    TRANSCRIBES_ONLY,
    CallerUtterance,
    EngineAudio,
    EngineClosed,
    VoiceEvent,
    VoiceSessionConfig,
)
from .speech import FIXTURE_SCHEME, LocalReplaySpeechAdapter

#: The deterministic pseudo-audio rate. 16 kHz mono, matching the boundary's native rate.
_SAY_RATE_HZ = 16_000

#: Pseudo-audio bytes voiced per character of text: a stand-in duration, not a codec.
_BYTES_PER_CHAR = 32


class ScriptedVoiceEngine:
    """Replay scripted caller utterances; echo injected text; record everything said."""

    speech_authorship = TRANSCRIBES_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._speech = LocalReplaySpeechAdapter(settings)

    async def connect(self, config: VoiceSessionConfig) -> ScriptedVoiceSession:
        rows: list[dict[str, object]] = []
        try:
            rows = self._speech.script(f"{FIXTURE_SCHEME}{config.contact.contact_id}")
        except FileNotFoundError:
            # No scripted stream for this contact: a live-driven session (tests push text via
            # ``send_caller_text``) rather than a replayed one. Both are deterministic.
            rows = []
        utterances = tuple(
            str(row.get("text", ""))
            for row in rows
            if str(row.get("role", "")) == "customer" and str(row.get("text", "")).strip()
        )
        return ScriptedVoiceSession(utterances)


class ScriptedVoiceSession:
    """The offline session: a queue of scripted then injected utterances, and a said-log."""

    def __init__(self, scripted: tuple[str, ...]) -> None:
        self._queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        for text in scripted:
            self._queue.put_nowait(CallerUtterance(text=text, final=True))
        self._said: list[str] = []
        self._tool_results: list[tuple[str, Mapping[str, object]]] = []
        self._caller_audio_bytes = 0
        self._interrupts = 0
        self._closed = False

    # ------------------------------------------------------------------ observability
    @property
    def said(self) -> tuple[str, ...]:
        """Every line voiced through :meth:`say`, in order. What the caller WOULD have heard."""
        return tuple(self._said)

    @property
    def tool_results(self) -> tuple[tuple[str, Mapping[str, object]], ...]:
        return tuple(self._tool_results)

    @property
    def caller_audio_bytes(self) -> int:
        return self._caller_audio_bytes

    @property
    def interrupts(self) -> int:
        return self._interrupts

    # ------------------------------------------------------------------ port surface
    async def send_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        self._caller_audio_bytes += len(pcm)

    async def send_caller_text(self, text: str) -> None:
        if not self._closed:
            self._queue.put_nowait(CallerUtterance(text=text, final=True))

    async def send_tool_result(self, call_id: str, result: Mapping[str, object]) -> None:
        self._tool_results.append((call_id, dict(result)))

    async def say(self, text: str) -> EngineAudio:
        self._said.append(text)
        return EngineAudio(
            pcm=b"\x00\x00" * (_BYTES_PER_CHAR * max(1, len(text)) // 2),
            sample_rate_hz=_SAY_RATE_HZ,
        )

    async def interrupt(self) -> None:
        self._interrupts += 1

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            item = await self._queue.get()
            if item is None:
                yield EngineClosed(reason="closed", resumable=False)
                return
            yield item

    async def close(self) -> str:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)
        return ""
