"""Byte-identical turn assembly across replays, and the channel that drives both modes.

The streaming layer is the one place where "deterministic" is easy to claim and easy to lose: a
recogniser is not deterministic, so the OFFLINE family replays a script instead, and this suite
is what makes the replay claim checkable rather than assumed.

The assertion is the kit's own canonical digest over the assembled transcript. Comparing
dataclasses would pass for two objects that serialise differently; comparing the digest is the
same question a downstream consumer asks when it re-verifies an exported record.
"""

from __future__ import annotations

import pytest
from speech_lexicon_kit import (
    AudioRef,
    ChannelRole,
    DiarizationRequest,
    SpeechSynthesisRequest,
    TranscriptionRequest,
    digest,
)

from contact_centre_conversations.adapters.local.channel import (
    LocalScriptedChannel,
)
from contact_centre_conversations.adapters.local.speech import (
    FIXTURE_SCHEME,
    LocalReplaySpeechAdapter,
)

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_AUDIO = AudioRef(uri=FIXTURE_SCHEME + sample_cases.CLEAN_CONTACT_ID, media_type="audio/wav")


def _transcribe() -> object:
    adapter = LocalReplaySpeechAdapter(local_settings())
    return adapter.transcribe(
        TranscriptionRequest(request_id="r", audio=_AUDIO, locale=sample_cases.LOCALE)
    ).transcript


def test_turn_assembly_is_byte_identical_across_replays() -> None:
    first, second = _transcribe(), _transcribe()
    assert digest(first) == digest(second), (
        "two replays of the same scripted contact produced different transcripts, so nothing "
        "downstream of the recogniser can be replayed either"
    )


def test_the_replayed_transcript_carries_roles_and_timings() -> None:
    """A digest that matched two empty transcripts would prove nothing."""
    transcript = _transcribe()
    assert transcript.turns, "the replay produced no turns"  # type: ignore[attr-defined]
    assert all(turn.role is not ChannelRole.UNKNOWN for turn in transcript.turns)  # type: ignore[attr-defined]
    assert all(turn.end_ms is not None for turn in transcript.turns)  # type: ignore[attr-defined]


def test_the_replayer_refuses_a_reference_it_cannot_replay() -> None:
    """A URI it cannot resolve must raise, never return an empty transcript."""
    adapter = LocalReplaySpeechAdapter(local_settings())
    with pytest.raises(ValueError, match="fixture://"):
        adapter.transcribe(
            TranscriptionRequest(
                request_id="r",
                audio=AudioRef(uri="gs://real-bucket/audio.wav", media_type="audio/wav"),
                locale=sample_cases.LOCALE,
            )
        )


def test_synthesis_and_diarization_answer_from_the_same_script() -> None:
    adapter = LocalReplaySpeechAdapter(local_settings())
    synth = adapter.synthesize(
        SpeechSynthesisRequest(request_id="s", text="Hello.", locale=sample_cases.LOCALE)
    )
    assert synth.audio.uri.startswith(FIXTURE_SCHEME)
    segments = adapter.diarize(DiarizationRequest(request_id="d", audio=_AUDIO)).segments
    assert segments, "diarization invented no speakers and found none either"
    assert all(segment.end_ms >= segment.start_ms for segment in segments)


def test_the_channel_yields_the_same_turns_and_marks_the_last_one() -> None:
    """The channel is what drives BOTH mode orchestrators offline, so it carries the end flag."""
    channel = LocalScriptedChannel(local_settings())
    turns = list(channel.turns(sample_cases.AGENT_CONTACT))
    assert turns
    assert [turn.index for turn in turns] == list(range(len(turns)))
    assert turns[-1].ends_contact is True, (
        "the last turn must close the contact, or no disclosure window ever closes"
    )
    assert all(turn.ends_contact is False for turn in turns[:-1])


def test_outbound_messages_are_collected_rather_than_sent() -> None:
    channel = LocalScriptedChannel(local_settings())
    channel.open(sample_cases.CUSTOMER_CONTACT)
    reference = channel.send(sample_cases.CUSTOMER_CONTACT, "Your balance is available.")
    assert reference
    assert channel.sent == (
        (sample_cases.CUSTOMER_CONTACT.contact_id, "Your balance is available."),
    )
