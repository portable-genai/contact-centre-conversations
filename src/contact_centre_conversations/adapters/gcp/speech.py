"""Managed speech adapters: streaming Speech-to-Text, Chirp synthesis and diarization.

Bound to all three speech ports. Every SDK import is LAZY, inside the method, so the offline
profiles import this module with no cloud SDK present, which is the whole reason the hard gate
can bind the managed family and still run with nothing installed.

The recogniser is pinned to the deployment's own region (``settings.region``) rather than a
global endpoint: audio is personal data, and a recogniser in another jurisdiction is a residency
breach that no amount of downstream masking undoes.
"""

from __future__ import annotations

from typing import Any

from speech_lexicon_kit import (
    AudioRef,
    ChannelRole,
    DiarizationRequest,
    DiarizationResult,
    SpeakerSegment,
    SpeakerTurn,
    SpeechSynthesisRequest,
    SynthesisResult,
    Transcript,
    TranscriptionRequest,
    TranscriptionResult,
)

from ...config import Settings


class CloudSpeechAdapter:
    """Managed recogniser, synthesiser and diarizer, region-pinned and lazily imported."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _speech_client(self) -> Any:
        from google.cloud import speech_v2  # noqa: PLC0415

        return speech_v2.SpeechClient()

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        client = self._speech_client()
        response = client.recognize(
            request={
                "recognizer": self._recognizer(),
                "uri": request.audio.uri,
                "config": {
                    "language_codes": [request.locale],
                    "features": {"enable_word_time_offsets": request.word_offsets},
                },
            }
        )
        turns = tuple(
            SpeakerTurn(
                index=index,
                speaker_id=f"channel-{index}",
                role=ChannelRole.UNKNOWN,
                text=result.alternatives[0].transcript if result.alternatives else "",
            )
            for index, result in enumerate(getattr(response, "results", ()))
        )
        return TranscriptionResult(
            transcript=Transcript(
                transcript_id=request.request_id,
                locale=request.locale,
                turns=turns,
                engine="google-speech-v2",
            )
        )

    def _recognizer(self) -> str:
        return f"projects/-/locations/{self._settings.region}/recognizers/_"

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesisResult:
        from google.cloud import texttospeech  # noqa: PLC0415

        client = texttospeech.TextToSpeechClient()
        response = client.synthesize_speech(
            input=texttospeech.SynthesisInput(text=request.text),
            voice=texttospeech.VoiceSelectionParams(
                language_code=request.locale, name=request.voice or None
            ),
            audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3),
        )
        return SynthesisResult(
            request_id=request.request_id,
            audio=AudioRef(
                uri=f"data:audio/mpeg;base64,{len(response.audio_content)}",
                media_type="audio/mpeg",
            ),
            voice=request.voice,
            characters_billed=len(request.text),
        )

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        client = self._speech_client()
        response = client.recognize(
            request={
                "recognizer": self._recognizer(),
                "uri": request.audio.uri,
                "config": {
                    "features": {
                        "diarization_config": {
                            "min_speaker_count": 2,
                            "max_speaker_count": request.expected_speakers or 2,
                        }
                    }
                },
            }
        )
        segments = tuple(
            SpeakerSegment(
                speaker_id=str(getattr(word, "speaker_label", "")) or "unknown",
                start_ms=int(getattr(word, "start_offset", 0)),
                end_ms=int(getattr(word, "end_offset", 0)),
            )
            for result in getattr(response, "results", ())
            for alternative in getattr(result, "alternatives", ())
            for word in getattr(alternative, "words", ())
        )
        return DiarizationResult(request_id=request.request_id, segments=segments)
