"""Shared deterministic speech synthesis for BOTH managed voice engines.

Whatever posture the engine declares, gated replies, disclosures, refusal lines and the kill
switch fallback are voiced HERE, from exact text the deterministic pipeline authored. The
Gemini Live engine cannot be made to speak an exact sentence (a native audio model paraphrases),
so deterministic prose never goes through it: it goes through Chirp synthesis, always.

The SDK import is LAZY and the endpoint is REGION-PINNED to the deployment region, which is
what keeps this half of the voice path inside the residency boundary even when the live engine
half is not (see docs/voice-gateway.md for that trade-off, stated rather than hidden).
"""

from __future__ import annotations

from ...config import Settings

#: The synthesis output rate. 24 kHz LINEAR16, matching the Gemini Live output rate so the
#: playout path downsamples exactly one ratio (3:1 to the 8 kHz telephone leg) for both sources.
SYNTH_RATE_HZ = 24_000

#: The RIFF/WAV header length the non-streaming LINEAR16 synthesis prepends. The gateway wants
#: raw frames, so the container header is removed once, here.
_RIFF_HEADER_BYTES = 44


def synthesize_pcm(text: str, settings: Settings) -> bytes:
    """Voice ``text`` and return raw 16-bit little-endian mono PCM at :data:`SYNTH_RATE_HZ`."""
    # Lazy: the offline profiles must import this module with no SDK present.
    from google.cloud import texttospeech  # noqa: PLC0415

    client = texttospeech.TextToSpeechClient(
        client_options={"api_endpoint": f"{settings.region}-texttospeech.googleapis.com"}
    )
    response = client.synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(
            language_code=_language_of(settings.voice.tts_voice),
            name=settings.voice.tts_voice,
        ),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=SYNTH_RATE_HZ,
        ),
    )
    audio = bytes(response.audio_content)
    if audio[:4] == b"RIFF":
        audio = audio[_RIFF_HEADER_BYTES:]
    return audio


def _language_of(voice_name: str) -> str:
    """The BCP-47 prefix of a managed voice name (``en-US-Chirp3-HD-Kore`` names ``en-US``)."""
    parts = voice_name.split("-")
    if len(parts) >= 2:
        return "-".join(parts[:2])
    return voice_name
