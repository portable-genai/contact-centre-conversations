"""The call orchestrator: engine events in, deterministic duties out, per declared posture.

Driven with a stub engine and a recording transport, so every duty is asserted against the
REAL self-service pipeline (real packs, real gate, real audit) with no socket and no SDK:

* behind a transcribe-only engine, the pipeline authors every spoken reply;
* behind an authoring engine, a refused turn trips the kill switch: playout flushed, the
  deterministic fallback spoken, the caller sent to a person;
* unsolicited audio from a transcribe-only engine is a defect that ends the call safely;
* a resumable engine close reconnects with the newest handle; a chat turn arriving mid-call
  is forwarded into the live session, redacted.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping

from speech_lexicon_kit import ChannelRole, SpeakerTurn

from contact_centre_conversations.config import Container, VoiceSettings, build_container
from contact_centre_conversations.domain.models import ContactChannel, ContactRef
from contact_centre_conversations.domain.modes import ContactMode
from contact_centre_conversations.ports.voice_engine import (
    AUTHORS_SPEECH,
    TRANSCRIBES_ONLY,
    CallerUtterance,
    EngineAudio,
    EngineClosed,
    EngineInterrupted,
    VoiceEvent,
    VoiceSessionConfig,
)
from contact_centre_conversations.services import build_services
from contact_centre_conversations.voice.session import FALLBACK_LINE, VoiceCallSession

from tests.conftest import local_settings
from tests.fixtures import sample_cases


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class StubSession:
    def __init__(self, scripted: list[VoiceEvent]) -> None:
        self.queue: asyncio.Queue[VoiceEvent | None] = asyncio.Queue()
        for event in scripted:
            self.queue.put_nowait(event)
        self.said: list[str] = []
        self.texts: list[str] = []
        self.interrupts = 0
        self.closed = False

    async def send_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        pass

    async def send_caller_text(self, text: str) -> None:
        self.texts.append(text)

    async def send_tool_result(self, call_id: str, result: Mapping[str, object]) -> None:
        pass

    async def say(self, text: str) -> EngineAudio:
        self.said.append(text)
        return EngineAudio(pcm=b"\x00\x00" * 8, sample_rate_hz=16_000)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def events(self) -> AsyncIterator[VoiceEvent]:
        while True:
            item = await self.queue.get()
            if item is None:
                yield EngineClosed(reason="closed")
                return
            yield item

    async def close(self) -> str:
        self.closed = True
        self.queue.put_nowait(None)
        return "handle-1"


class StubEngine:
    def __init__(self, authorship: str, scripts: list[list[VoiceEvent]]) -> None:
        self.speech_authorship = authorship
        self._scripts = scripts
        self.connects = 0
        self.configs: list[VoiceSessionConfig] = []
        self.sessions: list[StubSession] = []

    async def connect(self, config: VoiceSessionConfig) -> StubSession:
        self.configs.append(config)
        script = self._scripts[self.connects] if self.connects < len(self._scripts) else []
        self.connects += 1
        session = StubSession(list(script))
        self.sessions.append(session)
        return session


class RecordingTransport:
    def __init__(self) -> None:
        self.played: list[tuple[int, int]] = []  # (byte length, rate)
        self.flushes = 0
        self.transfers: list[str] = []
        self.hangups = 0

    def play_pcm(self, pcm: bytes, sample_rate_hz: int) -> None:
        self.played.append((len(pcm), sample_rate_hz))

    def flush_playout(self) -> None:
        self.flushes += 1

    def request_transfer(self, reason: str) -> None:
        self.transfers.append(reason)

    def hangup(self) -> None:
        self.hangups += 1


def _contact(contact_id: str = "voice-test-0001") -> ContactRef:
    return ContactRef(
        contact_id=contact_id,
        tenant=sample_cases.TENANT,
        market=sample_cases.MARKET,
        locale=sample_cases.LOCALE,
        vertical=sample_cases.VERTICAL,
        mode=ContactMode.SELF_SERVICE,
        channel=ContactChannel.VOICE,
    )


def _build(
    engine: StubEngine, *, chat_poll_ms: int = 0
) -> tuple[VoiceCallSession, RecordingTransport, Container]:
    container = build_container(local_settings(voice=VoiceSettings(chat_poll_ms=chat_poll_ms)))
    services = build_services(container)
    transport = RecordingTransport()
    session = VoiceCallSession(
        settings=container.settings,
        contact=_contact(),
        engine=engine,  # type: ignore[arg-type]
        service=services.self_service,
        store=container.contact_store,
        tools=container.tool_catalog,
        transport=transport,
    )
    return session, transport, container


async def _drive(session: VoiceCallSession) -> None:
    await session.start()
    await session.run()
    await session.stop()


# --------------------------------------------------------------------------- #
# Transcribe-only duties
# --------------------------------------------------------------------------- #
def test_greeting_then_pipeline_authored_reply_are_spoken() -> None:
    engine = StubEngine(
        TRANSCRIBES_ONLY,
        [[CallerUtterance(text="what is my card balance", final=True), EngineClosed("done")]],
    )
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    stub = engine.sessions[0]
    assert stub.said, "nothing was spoken at all"
    assert stub.said[0].startswith("You are connected"), "the greeting speaks first"
    assert len(stub.said) >= 2, "the caller's turn produced no spoken reply"
    assert len(transport.played) == len(stub.said), "everything said must reach playout"


def test_a_partial_utterance_is_not_a_turn() -> None:
    engine = StubEngine(
        TRANSCRIBES_ONLY,
        [[CallerUtterance(text="what is my", final=False), EngineClosed("done")]],
    )
    session, _, _ = _build(engine)
    asyncio.run(_drive(session))
    assert len(engine.sessions[0].said) == 1, "only the greeting: a partial must not be judged"


def test_unsolicited_audio_from_a_transcribe_only_engine_ends_the_call_safely() -> None:
    engine = StubEngine(
        TRANSCRIBES_ONLY,
        [[EngineAudio(pcm=b"\x00\x00" * 160, sample_rate_hz=24_000), EngineClosed("done")]],
    )
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    stub = engine.sessions[0]
    assert transport.flushes >= 1
    assert FALLBACK_LINE in stub.said
    assert transport.transfers, "a defective engine must hand the caller to a person"
    # The defect's audio itself never reached the caller: only say() lines were played.
    assert all(rate == 16_000 for _, rate in transport.played)


# --------------------------------------------------------------------------- #
# Authoring-engine duties (shadow gate)
# --------------------------------------------------------------------------- #
def test_engine_audio_plays_through_for_an_authoring_engine() -> None:
    engine = StubEngine(
        AUTHORS_SPEECH,
        [[EngineAudio(pcm=b"\x00\x00" * 160, sample_rate_hz=24_000), EngineClosed("done")]],
    )
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    assert (320, 24_000) in transport.played


def test_a_refused_turn_trips_the_kill_switch() -> None:
    engine = StubEngine(
        AUTHORS_SPEECH,
        [
            [
                CallerUtterance(text="zzz gibberish nothing matches this", final=True),
                EngineClosed("done"),
            ]
        ],
    )
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    stub = engine.sessions[0]
    assert transport.flushes >= 1, "queued model audio must be flushed on a refusal"
    assert stub.interrupts >= 1
    assert FALLBACK_LINE in stub.said
    assert transport.transfers, "a refused turn behind an authoring engine goes to a person"


def test_engine_audio_after_the_kill_switch_never_reaches_the_caller() -> None:
    """An authoring engine keeps streaming the refused turn's audio (it cannot be silenced);
    every frame after the kill switch must be DROPPED, not queued behind the fallback line."""
    engine = StubEngine(
        AUTHORS_SPEECH,
        [
            [
                CallerUtterance(text="zzz gibberish nothing matches this", final=True),
                # The model's refused speech, still arriving after the gate refused the turn.
                EngineAudio(pcm=b"\x11\x11" * 160, sample_rate_hz=24_000),
                EngineAudio(pcm=b"\x22\x22" * 160, sample_rate_hz=24_000),
                EngineClosed("done"),
            ]
        ],
    )
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    # Only the deterministic fallback line was voiced to the caller (16 kHz say() audio); no
    # 24 kHz model frame was ever played after the switch.
    assert transport.transfers
    assert all(rate == 16_000 for _, rate in transport.played), (
        "post-kill-switch model audio leaked to the caller"
    )


def test_barge_in_flushes_playout() -> None:
    engine = StubEngine(AUTHORS_SPEECH, [[EngineInterrupted(), EngineClosed("done")]])
    session, transport, _ = _build(engine)
    asyncio.run(_drive(session))
    assert transport.flushes == 1


# --------------------------------------------------------------------------- #
# Continuity
# --------------------------------------------------------------------------- #
def test_a_resumable_close_reconnects_with_the_newest_handle() -> None:
    engine = StubEngine(
        TRANSCRIBES_ONLY,
        [[EngineClosed("go-away", resumable=True)], [EngineClosed("done")]],
    )
    session, _, _ = _build(engine)
    asyncio.run(_drive(session))
    assert engine.connects == 2, "a resumable close must reconnect"


def test_dtmf_digits_run_the_same_pipeline_as_speech() -> None:
    engine = StubEngine(TRANSCRIBES_ONLY, [[]])
    session, _, container = _build(engine)

    async def drive() -> None:
        await session.start()
        await session.on_digits("1234")
        await session.stop()

    asyncio.run(drive())
    stub = engine.sessions[0]
    assert len(stub.said) >= 2, "a dialled string must be answered like an utterance"
    stored = container.contact_store.turns("voice-test-0001", tenant=sample_cases.TENANT)
    assert len(stored) == 1, "the dialled turn must be stored (redacted) like any other"


def test_a_chat_turn_arriving_mid_call_is_forwarded_redacted() -> None:
    engine = StubEngine(TRANSCRIBES_ONLY, [[]])
    session, _, container = _build(engine, chat_poll_ms=20)

    async def drive() -> None:
        await session.start()
        # The chat surface stores a redacted turn while the call is live.
        container.contact_store.create(_contact())
        container.contact_store.append_turn(
            "voice-test-0001",
            SpeakerTurn(
                index=0,
                speaker_id="customer",
                role=ChannelRole.CUSTOMER,
                text="you can reach me at jane.doe@example.com",
            ),
            tenant=sample_cases.TENANT,
        )
        await asyncio.sleep(0.1)
        await session.stop()

    asyncio.run(drive())
    stub = engine.sessions[0]
    assert stub.texts, "the mid-call chat turn never reached the live session"
    assert stub.texts[0].startswith("[via chat] ")
    assert "jane.doe@example.com" not in stub.texts[0], "the forwarded text must be redacted"


def test_the_callers_own_spoken_turn_is_not_echoed_back_as_chat() -> None:
    """The poll forwards CHAT turns, never the session's own spoken/keyed turns: a caller's
    utterance is stored (speaker CALLER) at the top of the pipeline, and the poll must skip it
    rather than read it back out and feed it to the engine as '[via chat] ...'."""
    engine = StubEngine(TRANSCRIBES_ONLY, [[]])
    session, _, _ = _build(engine, chat_poll_ms=20)

    async def drive() -> None:
        await session.start()
        await session.on_digits("1234")  # a caller turn: stored with speaker CALLER
        await asyncio.sleep(0.1)  # let several poll ticks run
        await session.stop()

    asyncio.run(drive())
    stub = engine.sessions[0]
    assert stub.texts == [], "the caller's own turn must never be forwarded back as chat"


def test_a_chat_forward_failure_does_not_end_forwarding_for_the_call() -> None:
    """One failing send must be logged and swallowed: the poll task survives so later chat
    turns still forward."""
    engine = StubEngine(TRANSCRIBES_ONLY, [[]])
    session, _, container = _build(engine, chat_poll_ms=20)
    calls = {"n": 0}
    original = engine.sessions  # not yet populated; patch after start

    async def drive() -> None:
        await session.start()
        stub = engine.sessions[0]
        real_send = stub.send_caller_text

        async def flaky(text: str) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient send failure")
            await real_send(text)

        stub.send_caller_text = flaky  # type: ignore[method-assign]
        container.contact_store.create(_contact())
        for i, text in enumerate(("first chat turn", "second chat turn")):
            container.contact_store.append_turn(
                "voice-test-0001",
                SpeakerTurn(index=i, speaker_id="webchat", role=ChannelRole.CUSTOMER, text=text),
                tenant=sample_cases.TENANT,
            )
            await asyncio.sleep(0.06)
        await session.stop()

    asyncio.run(drive())
    stub = engine.sessions[0]
    assert calls["n"] >= 2, "the poll task died on the first failure instead of surviving"
    assert any("second chat turn" in t for t in stub.texts), "later chat turns stopped forwarding"
    del original


def test_context_turns_seed_the_engine_at_connect() -> None:
    engine = StubEngine(TRANSCRIBES_ONLY, [[]])
    container = build_container(local_settings(voice=VoiceSettings(chat_poll_ms=0)))
    container.contact_store.create(_contact())
    container.contact_store.append_turn(
        "voice-test-0001",
        SpeakerTurn(index=0, speaker_id="customer", role=ChannelRole.CUSTOMER, text="earlier"),
        tenant=sample_cases.TENANT,
    )
    services = build_services(container)
    transport = RecordingTransport()
    session = VoiceCallSession(
        settings=container.settings,
        contact=_contact(),
        engine=engine,  # type: ignore[arg-type]
        service=services.self_service,
        store=container.contact_store,
        tools=container.tool_catalog,
        transport=transport,
    )

    async def drive() -> None:
        await session.start()
        await session.stop()

    asyncio.run(drive())
    assert engine.configs[0].context_turns, "the stored transcript must seed the session"
    assert engine.configs[0].context_turns[0].text == "earlier"
