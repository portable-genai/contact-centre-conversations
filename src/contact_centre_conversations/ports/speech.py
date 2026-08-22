"""Speech ports: RE-EXPORTED from ``speech-lexicon-kit``, never redeclared here.

Streaming speech-to-text, synthesis and diarization are the same boundary in E1, E3, E5 and
every other repo that touches a contact centre. The kit owns the Protocols and the request and
result types; this module exists so that the hexagon still has ONE import site for its port set
and so ``PORT_PROTOCOLS`` can name them.

Redeclaring ``SpeechToTextPort`` here would create a second, drifting definition of the same
boundary, and the first time the two disagreed the adapters would satisfy one of them.
"""

from __future__ import annotations

from speech_lexicon_kit.ports import (
    AudioRef,
    ChannelRoleBinding,
    DiarizationPort,
    DiarizationRequest,
    DiarizationResult,
    SpeechSynthesisRequest,
    SpeechToTextPort,
    SynthesisResult,
    TextToSpeechPort,
    TranscriptionRequest,
    TranscriptionResult,
)

__all__ = [
    "AudioRef",
    "ChannelRoleBinding",
    "DiarizationPort",
    "DiarizationRequest",
    "DiarizationResult",
    "SpeechSynthesisRequest",
    "SpeechToTextPort",
    "SynthesisResult",
    "TextToSpeechPort",
    "TranscriptionRequest",
    "TranscriptionResult",
]
