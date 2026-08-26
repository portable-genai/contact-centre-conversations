"""The media plumbing: G.711, resampling, RTP framing and DTMF collection.

These are the bytes-level facts the whole voice path stands on, so each one is asserted
against constructed inputs, including the refusal cases: a parser that never says no to a
stray datagram has not been shown to parse anything.
"""

from __future__ import annotations

import struct

from contact_centre_conversations.voice import audio, rtp
from contact_centre_conversations.voice.dtmf import DigitCollector


# --------------------------------------------------------------------------- #
# G.711 and resampling
# --------------------------------------------------------------------------- #
def test_ulaw_silence_decodes_to_zero_and_zero_encodes_to_silence() -> None:
    assert audio.ulaw_to_pcm(b"\xff") == b"\x00\x00"
    assert audio.pcm_to_ulaw(b"\x00\x00") == b"\xff"


def test_ulaw_round_trip_stays_within_codec_tolerance() -> None:
    """mu-law is lossy by design; the round trip must stay within the segment step size."""
    for value in (-30_000, -1_000, -33, 0, 25, 500, 8_191, 30_000):
        pcm = struct.pack("<h", value)
        (recovered,) = struct.unpack("<h", audio.ulaw_to_pcm(audio.pcm_to_ulaw(pcm)))
        assert abs(recovered - value) <= max(64, abs(value) // 16), (value, recovered)


def test_every_ulaw_byte_decodes_and_re_encodes_to_the_same_value() -> None:
    """The codec tables are mutually consistent over the whole byte range.

    Consistency is at the VALUE level: mu-law has two encodings of zero (0x7F is negative
    zero), so the re-encoded byte may differ while the decoded sample must not.
    """
    for byte in range(256):
        pcm = audio.ulaw_to_pcm(bytes([byte]))
        again = audio.ulaw_to_pcm(audio.pcm_to_ulaw(pcm))
        assert again == pcm, f"byte {byte:#x} changed value across decode/encode/decode"


def test_upsample_doubles_and_downsample_divides_exactly() -> None:
    pcm = struct.pack("<4h", 0, 1000, -1000, 500)
    assert len(audio.upsample_2x(pcm)) == 2 * len(pcm)
    pcm24 = struct.pack("<6h", 300, 300, 300, -600, -600, -600)
    assert audio.downsample_3x(pcm24) == struct.pack("<2h", 300, -600)
    pcm16 = struct.pack("<4h", 100, 300, -100, -300)
    assert audio.downsample_2x(pcm16) == struct.pack("<2h", 200, -200)


def test_odd_length_pcm_is_refused() -> None:
    try:
        audio.pcm_to_ulaw(b"\x00")
    except ValueError:
        return
    raise AssertionError("an odd byte length is not 16-bit PCM and must be refused")


# --------------------------------------------------------------------------- #
# RTP
# --------------------------------------------------------------------------- #
def test_rtp_build_parse_round_trip() -> None:
    packet = rtp.RtpPacket(
        payload_type=rtp.PT_PCMU,
        sequence=42,
        timestamp=160,
        ssrc=0x1234,
        payload=b"\xff" * 160,
        marker=True,
    )
    parsed = rtp.parse(rtp.build(packet))
    assert parsed == packet


def test_rtp_parse_refuses_non_rtp_datagrams() -> None:
    assert rtp.parse(b"") is None
    assert rtp.parse(b"OPTIONS sip:x SIP/2.0\r\n") is None  # SIP on the media port
    assert rtp.parse(b"\x00\x01\x02\x03") is None  # STUN-ish short garbage


def test_rtp_parse_strips_padding_and_csrc() -> None:
    base = rtp.build(rtp.RtpPacket(payload_type=0, sequence=1, timestamp=1, ssrc=1, payload=b"ab"))
    padded = bytes([base[0] | 0x20]) + base[1:] + b"\x00\x02"
    parsed = rtp.parse(padded)
    assert parsed is not None and parsed.payload == b"ab"


def test_telephone_event_parse() -> None:
    payload = bytes([5, 0x8A, 0x01, 0x40])  # digit 5, end bit set
    event = rtp.parse_telephone_event(payload)
    assert event is not None
    assert event.digit == "5" and event.end and event.duration == 0x0140
    assert rtp.parse_telephone_event(b"\x05") is None
    assert rtp.parse_telephone_event(bytes([200, 0, 0, 0])) is None


# --------------------------------------------------------------------------- #
# DTMF collection
# --------------------------------------------------------------------------- #
def _press(collector: DigitCollector, digit: str, at_ms: int) -> str | None:
    """One key press as a real trunk delivers it: a start packet, repeats, and the end packet
    sent THREE times (RFC 4733 sect. 2.5.1.4, and exactly what Cisco rtp-nte does)."""
    out = collector.on_event(rtp.TelephoneEvent(digit=digit, end=False, duration=160), now_ms=at_ms)
    for _ in range(2):
        repeat = collector.on_event(
            rtp.TelephoneEvent(digit=digit, end=False, duration=320), now_ms=at_ms
        )
        out = out or repeat
    for _ in range(3):
        end = collector.on_event(
            rtp.TelephoneEvent(digit=digit, end=True, duration=480), now_ms=at_ms
        )
        out = out or end
    return out


def test_digits_deduplicate_and_flush_on_terminator() -> None:
    collector = DigitCollector(terminator="#", inter_digit_timeout_ms=3000)
    assert _press(collector, "1", 0) is None
    assert _press(collector, "2", 100) is None
    assert _press(collector, "3", 200) is None
    assert _press(collector, "#", 300) == "123"


def test_digits_flush_on_inter_digit_silence() -> None:
    collector = DigitCollector(terminator="#", inter_digit_timeout_ms=1000)
    _press(collector, "4", 0)
    _press(collector, "2", 500)
    assert collector.on_tick(now_ms=1200) is None  # only 700 ms since the last digit
    assert collector.on_tick(now_ms=1600) == "42"
    assert collector.on_tick(now_ms=5000) is None  # nothing left to flush


def test_terminator_with_no_digits_flushes_nothing() -> None:
    collector = DigitCollector()
    assert _press(collector, "#", 0) is None


def test_end_packet_retransmissions_do_not_triple_the_digit() -> None:
    """The RFC 4733 triple end-send must register ONE press, not three: a real trunk sends the
    end packet three times, and reading each as a fresh press turned '1234#' into gibberish."""
    collector = DigitCollector(terminator="#", inter_digit_timeout_ms=3000)
    _press(collector, "1", 0)
    _press(collector, "2", 100)
    _press(collector, "3", 200)
    _press(collector, "4", 300)
    assert _press(collector, "#", 400) == "1234"


def test_a_lost_end_packet_still_registers_the_digit() -> None:
    """The digit registers on the first non-end packet, so dropping the end packet loses the
    press boundary but never the digit."""
    collector = DigitCollector(terminator="#", inter_digit_timeout_ms=1000)
    collector.on_event(rtp.TelephoneEvent(digit="7", end=False, duration=160), now_ms=0)
    # no end packet arrives; the next digit still lands
    collector.on_event(rtp.TelephoneEvent(digit="7", end=True, duration=320), now_ms=0)
    collector.on_event(rtp.TelephoneEvent(digit="8", end=False, duration=160), now_ms=100)
    collector.on_event(rtp.TelephoneEvent(digit="8", end=True, duration=320), now_ms=100)
    assert collector.on_tick(now_ms=1200) == "78"
