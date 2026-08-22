"""On-prem speech adapters: fail-fast portability placeholders (P-12).

Bound to all three speech ports. The client runs its own recogniser, its own synthesis and its
own diarization, on premises, and each refuses rather than returning an empty transcript: an
empty transcript is a legitimate result for a silent contact, so a placeholder that produced one
would look like a working stack recording nothing.
"""

from __future__ import annotations

from speech_lexicon_kit import (
    DiarizationRequest,
    DiarizationResult,
    SpeechSynthesisRequest,
    SynthesisResult,
    TranscriptionRequest,
    TranscriptionResult,
)

from ...config import Settings

_MESSAGE = (
    "on-prem speech is a portability placeholder: bind the client's own recogniser, synthesis "
    "and diarization (see docs/onprem-migration.md)."
)


class OnPremSpeechAdapter:
    """Satisfies the three speech ports but refuses on every method."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        raise NotImplementedError(_MESSAGE)

    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesisResult:
        raise NotImplementedError(_MESSAGE)

    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        raise NotImplementedError(_MESSAGE)
