"""G.711 mu-law transcoding and sample-rate conversion, standard library only.

The telephone leg is 8 kHz G.711 mu-law (the codec every Cisco dial-peer offers by default);
the engine boundary is 16 kHz PCM in and 24 kHz PCM out (the Live API contract, which the
deterministic synthesis path deliberately matches). Every conversion this gateway ever needs
is therefore one of three fixed, exact ratios:

* decode mu-law, then upsample 8 kHz to 16 kHz (1:2, linear interpolation) toward the engine;
* downsample 24 kHz to 8 kHz (3:1, mean of each triple as a crude anti-alias low-pass), then
  encode mu-law toward the caller;
* downsample 16 kHz to 8 kHz (2:1, mean of each pair) for the offline engine's pseudo-audio.

The standard library's ``audioop`` was removed in Python 3.13, so the two dozen lines it would
have contributed are written out here instead of pinning a deprecated module. Reference-grade
by declaration: a mean-of-N decimator is not a polyphase filter, and docs/voice-gateway.md says
so. It is deterministic, dependency-free and comfortably real-time in pure Python at telephone
rates, which is what this gateway optimizes for.

PCM everywhere below means 16-bit little-endian signed mono.
"""

from __future__ import annotations

import array
import sys

#: One RTP packet's worth of telephone audio: 20 ms at 8 kHz.
FRAME_MS = 20
TELEPHONE_RATE_HZ = 8_000
ENGINE_INPUT_RATE_HZ = 16_000
SAMPLES_PER_FRAME = TELEPHONE_RATE_HZ * FRAME_MS // 1000  # 160 mu-law bytes per packet

#: 20 ms of mu-law silence (0xFF encodes linear 0 in mu-law).
SILENCE_ULAW_FRAME = b"\xff" * SAMPLES_PER_FRAME

_BIAS = 0x84
_CLIP = 32_635


def _build_decode_table() -> tuple[int, ...]:
    values = []
    for byte in range(256):
        complement = ~byte & 0xFF
        sign = complement & 0x80
        exponent = (complement >> 4) & 0x07
        mantissa = complement & 0x0F
        sample = ((mantissa << 3) + _BIAS) << exponent
        sample -= _BIAS
        values.append(-sample if sign else sample)
    return tuple(values)


_DECODE = _build_decode_table()


def ulaw_to_pcm(data: bytes) -> bytes:
    """Decode G.711 mu-law bytes to PCM at the same 8 kHz rate."""
    out = array.array("h", (_DECODE[b] for b in data))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def pcm_to_ulaw(pcm: bytes) -> bytes:
    """Encode PCM (8 kHz) to G.711 mu-law bytes."""
    samples = _as_samples(pcm)
    encoded = bytearray(len(samples))
    for i, sample in enumerate(samples):
        sign = 0x80 if sample < 0 else 0
        magnitude = min(-sample if sample < 0 else sample, _CLIP) + _BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not magnitude & mask:
            exponent -= 1
            mask >>= 1
        mantissa = (magnitude >> (exponent + 3)) & 0x0F
        encoded[i] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return bytes(encoded)


def upsample_2x(pcm: bytes) -> bytes:
    """8 kHz to 16 kHz by linear interpolation between neighbouring samples."""
    samples = _as_samples(pcm)
    if not samples:
        return b""
    out = array.array("h")
    previous = samples[0]
    for sample in samples:
        out.append((previous + sample) >> 1)
        out.append(sample)
        previous = sample
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def downsample_3x(pcm: bytes) -> bytes:
    """24 kHz to 8 kHz: the mean of each triple, a crude but honest anti-alias low-pass."""
    return _decimate(pcm, 3)


def downsample_2x(pcm: bytes) -> bytes:
    """16 kHz to 8 kHz: the mean of each pair."""
    return _decimate(pcm, 2)


def _decimate(pcm: bytes, factor: int) -> bytes:
    samples = _as_samples(pcm)
    usable = len(samples) - (len(samples) % factor)
    out = array.array("h")
    for start in range(0, usable, factor):
        out.append(sum(samples[start : start + factor]) // factor)
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


def _as_samples(pcm: bytes) -> array.array[int]:
    if len(pcm) % 2:
        raise ValueError("PCM byte length must be even (16-bit samples)")
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples
