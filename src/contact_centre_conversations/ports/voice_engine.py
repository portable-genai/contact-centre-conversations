"""VoiceEnginePort: the realtime conversational engine behind a live voice call.

The telephony gateway terminates SIP and RTP and owns the caller's audio; this port is the seam
between that gateway and whatever turns audio into a conversation. Two managed engines implement
it, and the difference between them is a RISK POSTURE, not a vendor detail:

* the CASCADE engine streams speech to text and returns finalized caller utterances. It authors
  no speech of its own: the deterministic self-service pipeline decides every reply and the
  engine merely voices what it is told to :meth:`VoiceEngineSession.say`.
* the GEMINI LIVE engine is a native audio-to-audio model session. It speaks with its own voice,
  which means the model authors the words a customer hears. The session orchestrator therefore
  runs the SAME deterministic pipeline as a shadow gate over the live transcript and kills the
  engine's audio the moment a verdict refuses.

The engine DECLARES which of those it is (:data:`SPEECH_AUTHORSHIP_ATTR`), exactly the way an
identity adapter declares its end-user authentication: the orchestrator derives its duties from
the declaration, and silence is read as the safe case for the CALLER, which is
:data:`TRANSCRIBES_ONLY`. Under that reading an undeclared engine's unsolicited audio is a
defect to refuse, never something to play to a member of the public.

Audio crosses this boundary as raw 16-bit little-endian PCM ``bytes`` plus an explicit sample
rate, and it crosses ONLY here: the domain reads turns, never audio, and the kit speech ports
stay batch-shaped. This port is deliberately E1-local until a second repo needs a realtime
boundary, at which point the TYPES (not the sockets) graduate to ``speech-lexicon-kit``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from speech_lexicon_kit import SpeakerTurn

from ..domain.models import ContactRef

#: The class attribute a voice engine sets to declare who authors the caller-facing speech.
#: A CLASS attribute, readable without constructing the adapter, for the same reason the
#: identity declaration is one: duties derived from the declaration must be derivable before
#: any session exists.
SPEECH_AUTHORSHIP_ATTR = "speech_authorship"

#: The engine speaks with its own voice: the model authors the words the caller hears. The
#: orchestrator must shadow-gate every utterance and hold the kill switch.
AUTHORS_SPEECH = "authors-speech"

#: The engine only turns caller audio into text. Every spoken reply is authored by the
#: deterministic pipeline and voiced through :meth:`VoiceEngineSession.say`.
TRANSCRIBES_ONLY = "transcribes-only"

#: Every declaration this service understands. Anything else reads as :data:`TRANSCRIBES_ONLY`.
SPEECH_AUTHORSHIP_KINDS: frozenset[str] = frozenset({AUTHORS_SPEECH, TRANSCRIBES_ONLY})


def declared_speech_authorship(engine: object) -> str:
    """What ``engine`` (a class or an instance) declares, defaulting to the caller-safe case.

    The default is :data:`TRANSCRIBES_ONLY` because the two duties fail in opposite directions:
    treating an authoring engine as transcribe-only DROPS its unsolicited audio (the caller
    hears silence and the defect is loud), while treating a transcribe-only engine as authoring
    would wait for model audio that never comes AND stand ready to play unvetted speech. A typo
    in the declaration must land on the first of those, never the second.
    """
    declared = getattr(engine, SPEECH_AUTHORSHIP_ATTR, None)
    if isinstance(declared, str) and declared in SPEECH_AUTHORSHIP_KINDS:
        return declared
    return TRANSCRIBES_ONLY


# --------------------------------------------------------------------------------------- #
# Session configuration
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class VoiceToolSpec:
    """One action the engine may REQUEST. Requesting is all it may do: execution stays behind
    the deterministic action gate, which is what makes handing the model a tool list safe."""

    action_id: str
    description: str
    parameter_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VoiceSessionConfig:
    """Everything an engine needs to open one call session.

    ``context_turns`` carry the contact's stored, REDACTED transcript so a caller who moves
    between chat and voice keeps their thread. They are redacted by construction: the store
    never holds a raw identifier, so seeding an engine from the store cannot leak one.
    """

    contact: ContactRef
    system_prompt: str = ""
    context_turns: tuple[SpeakerTurn, ...] = ()
    tools: tuple[VoiceToolSpec, ...] = ()
    #: A resumption handle from a previous session of the SAME contact, or empty. Engines that
    #: cannot resume ignore it; the Gemini Live engine reconnects with it after a ``goAway``.
    resume_handle: str = ""


# --------------------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class EngineAudio:
    """Engine-initiated speech audio (an authoring model's own voice). Solicited speech from
    :meth:`VoiceEngineSession.say` is RETURNED by that call instead, so the orchestrator can
    tell the two apart without trusting a flag."""

    pcm: bytes
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class CallerUtterance:
    """What the engine heard the caller say. ``final`` marks an endpointed utterance; only a
    final utterance becomes a turn, because a half-heard sentence must not reach the gate."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True)
class EngineUtterance:
    """What an AUTHORING engine actually said, as text. The shadow gate reads these."""

    text: str


@dataclass(frozen=True, slots=True)
class EngineToolCall:
    """The engine asked for an action. The deterministic action gate decides, never the engine."""

    call_id: str
    action_id: str
    parameters: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class EngineInterrupted:
    """The engine detected caller barge-in: the orchestrator must flush queued playout."""


@dataclass(frozen=True, slots=True)
class EngineResumptionHandle:
    """A fresher resumption handle for this session. The orchestrator keeps the newest one."""

    handle: str


@dataclass(frozen=True, slots=True)
class EngineClosed:
    """The engine ended the session. ``resumable`` says a reconnect with the newest handle is
    expected (a Gemini Live ``goAway``), during which the gateway must keep RTP flowing;
    ``failure`` distinguishes a session that DIED from one that ended, because the caller is
    owed an apology and a person for the first and nothing for the second."""

    reason: str
    resumable: bool = False
    failure: bool = False


#: Everything :meth:`VoiceEngineSession.events` may yield.
VoiceEvent = (
    EngineAudio
    | CallerUtterance
    | EngineUtterance
    | EngineToolCall
    | EngineInterrupted
    | EngineResumptionHandle
    | EngineClosed
)


# --------------------------------------------------------------------------------------- #
# The port
# --------------------------------------------------------------------------------------- #
@runtime_checkable
class VoiceEngineSession(Protocol):
    """One live call's engine session. Async by nature: audio does not wait its turn."""

    async def send_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
        """Push one frame of caller audio (16-bit little-endian mono PCM)."""
        ...

    async def send_caller_text(self, text: str) -> None:
        """Push a caller TEXT turn into the live session (chat alongside voice, or DTMF).

        Callers of this method redact first: unlike raw audio, text CAN pass the PII boundary
        before a model sees it, so it must.
        """
        ...

    async def send_tool_result(self, call_id: str, result: Mapping[str, object]) -> None:
        """Answer one :class:`EngineToolCall` with the gate's outcome."""
        ...

    async def say(self, text: str) -> EngineAudio:
        """Deterministically voice ``text`` and return the audio. This is the ONLY way gated
        replies, disclosures and refusal lines become speech, in both engine postures."""
        ...

    async def interrupt(self) -> None:
        """Stop any in-flight engine speech (the orchestrator's kill switch and barge-in)."""
        ...

    def events(self) -> AsyncIterator[VoiceEvent]:
        """The engine's event stream. Ends after :class:`EngineClosed` or :meth:`close`."""
        ...

    async def close(self) -> str:
        """End the session and return the newest resumption handle, or empty."""
        ...


@runtime_checkable
class VoiceEnginePort(Protocol):
    """Open realtime engine sessions for voice calls."""

    async def connect(self, config: VoiceSessionConfig) -> VoiceEngineSession:
        """Open one session. A managed engine that is unconfigured or has no SDK REFUSES here,
        loudly, rather than answering the first frame with a surprise."""
        ...
