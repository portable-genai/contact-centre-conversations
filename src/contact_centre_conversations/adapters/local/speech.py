"""Local speech adapters: a deterministic offline streamer, bound to all three speech ports.

One class satisfies ``SpeechToTextPort``, ``TextToSpeechPort`` and ``DiarizationPort``, and the
container binds it three times. Three ports, one offline implementation, because the offline
behaviour of all three is the same kind of thing: replay what a fixture says happened.

Transcription replays scripted turns from ``config/streams/`` keyed by the audio URI, so the
same contact produces byte-identical turn assembly on every run (the replay digest test asserts
exactly that). Synthesis returns a deterministic pseudo-audio reference and never writes a file;
diarization derives segments from the replayed turns rather than inventing speakers.
"""

from __future__ import annotations

import json
from pathlib import Path

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

#: The scheme a fixture audio URI uses. Anything else is not something this adapter can replay.
FIXTURE_SCHEME = "fixture://"


class LocalReplaySpeechAdapter:
    """Replay scripted contacts; satisfy all three speech ports with no SDK and no audio."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._root = Path(settings.streams_path) if settings.streams_path else None

    # ----------------------------------------------------------------- STT
    def script(self, uri: str) -> list[dict[str, object]]:
        """The scripted rows behind one fixture URI. Public: the channel adapter replays them."""
        if not uri.startswith(FIXTURE_SCHEME):
            raise ValueError(
                f"the offline speech adapter replays {FIXTURE_SCHEME}<name> references only; "
                f"got {uri!r}. Bind the managed adapter to transcribe real audio."
            )
        if self._root is None:
            raise RuntimeError("no streams_path is configured, so nothing can be replayed")
        path = self._root / f"{uri[len(FIXTURE_SCHEME) :]}.jsonl"
        if not path.exists():
            raise FileNotFoundError(f"no scripted stream at {path}")
        rows: list[dict[str, object]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            rows.append(dict(json.loads(line)))
        return rows

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResult:
        rows = self.script(request.audio.uri)
        turns = tuple(
            SpeakerTurn(
                index=index,
                speaker_id=str(row.get("speaker_id", f"s{index}")),
                role=ChannelRole(str(row.get("role", "unknown"))),
                text=str(row.get("text", "")),
                start_ms=_as_int(row.get("start_ms")),
                end_ms=_as_int(row.get("end_ms")),
            )
            for index, row in enumerate(rows)
        )
        return TranscriptionResult(
            transcript=Transcript(
                transcript_id=request.request_id,
                locale=request.locale,
                turns=turns,
                engine="local-replay",
            )
        )

    # ----------------------------------------------------------------- TTS
    def synthesize(self, request: SpeechSynthesisRequest) -> SynthesisResult:
        return SynthesisResult(
            request_id=request.request_id,
            audio=AudioRef(
                uri=f"{FIXTURE_SCHEME}synth/{request.request_id}",
                media_type=request.audio_encoding,
            ),
            voice=request.voice or "offline-replay",
            characters_billed=len(request.text),
        )

    # --------------------------------------------------------- diarization
    def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        rows = self.script(request.audio.uri)
        segments = tuple(
            SpeakerSegment(
                speaker_id=str(row.get("speaker_id", f"s{index}")),
                start_ms=_as_int(row.get("start_ms")) or 0,
                end_ms=_as_int(row.get("end_ms")) or 0,
                role=ChannelRole(str(row.get("role", "unknown"))),
            )
            for index, row in enumerate(rows)
            if _as_int(row.get("end_ms"))
        )
        return DiarizationResult(request_id=request.request_id, segments=segments)


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))
