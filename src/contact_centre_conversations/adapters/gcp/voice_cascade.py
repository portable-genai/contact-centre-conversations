"""Managed CASCADE voice engine: streaming recognition in, deterministic synthesis out.

The residency-clean, invariant-clean voice path. Caller audio streams to Cloud Speech-to-Text
v2 (region-pinned, so a Singapore deployment keeps recognition in ``asia-southeast1``); every
finalized utterance comes back as a :class:`CallerUtterance` for the deterministic self-service
pipeline to judge; the reply that pipeline authors is voiced by :func:`synthesize_pcm`. The
model in the middle of THIS path is the same grounded, schema-bound drafting the chat path
uses: nothing about being a phone call changes who decides.

It declares :data:`TRANSCRIBES_ONLY` because that is the fact: this engine authors no speech.

The SDK imports are LAZY, inside :meth:`connect`, so the offline profiles import this module
with no cloud SDK installed, and offline the connect call REFUSES with the honest ImportError
rather than pretending a recognizer is listening.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue as thread_queue
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

from ...config import Settings
from ...ports.voice_engine import (
    TRANSCRIBES_ONLY,
    CallerUtterance,
    EngineAudio,
    EngineClosed,
    VoiceEvent,
    VoiceSessionConfig,
)
from ._voice_synth import SYNTH_RATE_HZ, synthesize_pcm

#: The rate this engine expects caller audio at. The boundary's native rate.
_INPUT_RATE_HZ = 16_000


class CascadeVoiceEngine:
    """Streaming STT session per call; synthesis on demand; no speech of its own."""

    speech_authorship = TRANSCRIBES_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def connect(self, config: VoiceSessionConfig) -> CascadeVoiceSession:
        # Lazy: the offline profiles must import this module with no SDK present, and an
        # offline connect must refuse loudly, not open a session nothing is behind.
        from google.cloud import speech_v2  # noqa: PLC0415

        return CascadeVoiceSession(self._settings, config, speech_v2)


class CascadeVoiceSession:
    """Bridge one call's audio into a blocking gRPC recognition stream, off the event loop."""

    def __init__(self, settings: Settings, config: VoiceSessionConfig, speech_v2: Any) -> None:
        self._settings = settings
        self._config = config
        self._speech_v2 = speech_v2
        self._audio: thread_queue.Queue[bytes | None] = thread_queue.Queue()
        self._events: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        self._closed = False
        self._recognizer = asyncio.create_task(asyncio.to_thread(self._recognize))

    # ------------------------------------------------------------------ port surface
    async def send_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        if sample_rate_hz != _INPUT_RATE_HZ:
            raise ValueError(
                f"the cascade engine expects {_INPUT_RATE_HZ} Hz caller audio, "
                f"got {sample_rate_hz} Hz; resample at the gateway boundary"
            )
        if not self._closed:
            self._audio.put(pcm)

    async def send_caller_text(self, text: str) -> None:
        """Text turns bypass recognition entirely: they are already what recognition makes."""
        if not self._closed:
            self._events.put_nowait(CallerUtterance(text=text, final=True))

    async def send_tool_result(self, call_id: str, result: Mapping[str, object]) -> None:
        """This engine never requests a tool, so a result addressed to it is a wiring defect."""
        raise RuntimeError(
            "the cascade engine issues no tool calls; a tool result reached the wrong engine"
        )

    async def say(self, text: str) -> EngineAudio:
        pcm = await asyncio.to_thread(synthesize_pcm, text, self._settings)
        return EngineAudio(pcm=pcm, sample_rate_hz=SYNTH_RATE_HZ)

    async def interrupt(self) -> None:
        """Nothing to stop engine-side: this engine has no voice. Playout is the gateway's."""

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            item = await self._events.get()
            if item is None:
                yield EngineClosed(reason="closed", resumable=False)
                return
            yield item

    async def close(self) -> str:
        if not self._closed:
            self._closed = True
            self._audio.put(None)
            self._events.put_nowait(None)
            with contextlib.suppress(Exception):
                await self._recognizer
        return ""

    # ------------------------------------------------------------------ recognition thread
    def _requests(self) -> Iterator[Any]:
        speech = self._speech_v2
        recognizer = (
            f"projects/{self._settings.project_id or '-'}/locations/"
            f"{self._settings.region}/recognizers/_"
        )
        yield speech.StreamingRecognizeRequest(
            recognizer=recognizer,
            streaming_config=speech.StreamingRecognitionConfig(
                config=speech.RecognitionConfig(
                    explicit_decoding_config=speech.ExplicitDecodingConfig(
                        encoding=speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                        sample_rate_hertz=_INPUT_RATE_HZ,
                        audio_channel_count=1,
                    ),
                    language_codes=[self._config.contact.locale],
                    model=self._settings.voice.stt_model,
                ),
                streaming_features=speech.StreamingRecognitionFeatures(
                    interim_results=True,
                ),
            ),
        )
        while True:
            chunk = self._audio.get()
            if chunk is None:
                return
            yield speech.StreamingRecognizeRequest(audio=chunk)

    def _recognize(self) -> None:
        """Run the blocking stream and post utterances back onto the event loop."""
        try:
            client = self._speech_v2.SpeechClient(
                client_options={"api_endpoint": f"{self._settings.region}-speech.googleapis.com"}
            )
            for response in client.streaming_recognize(requests=self._requests()):
                for result in response.results:
                    if not result.alternatives:
                        continue
                    text = str(result.alternatives[0].transcript).strip()
                    if not text:
                        continue
                    event = CallerUtterance(text=text, final=bool(result.is_final))
                    self._loop.call_soon_threadsafe(self._events.put_nowait, event)
        except Exception as exc:  # noqa: BLE001 - a dead recognizer must surface as an event
            if not self._closed:
                closed = EngineClosed(
                    reason=f"recognition failed: {type(exc).__name__}", failure=True
                )
                self._loop.call_soon_threadsafe(self._events.put_nowait, closed)
