"""RTP packet mechanics: header build and parse, plus RFC 4733 telephone-events (DTMF).

Pure functions over bytes. The gateway owns the socket and the 20 ms pacing; this module owns
the wire format, so it can be tested byte-for-byte with no network in the room.

Scope, stated: plain RTP (no SRTP; a production deployment terminates SRTP on the SBC in front,
see docs/cisco-connection-guide.md), PCMU payload type 0, and telephone-event per RFC 4733 on
the dynamically negotiated payload type (Cisco convention 101). RTCP is not implemented; the
gateway keeps the media path alive by SENDING continuous RTP (silence while listening), which
is the stream CUBE's default media-inactivity detection watches.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Static payload type for G.711 mu-law.
PT_PCMU = 0

_HEADER = struct.Struct("!BBHII")
_VERSION = 2 << 6


@dataclass(frozen=True, slots=True)
class RtpPacket:
    payload_type: int
    sequence: int
    timestamp: int
    ssrc: int
    payload: bytes
    marker: bool = False


def build(packet: RtpPacket) -> bytes:
    byte1 = packet.payload_type & 0x7F
    if packet.marker:
        byte1 |= 0x80
    header = _HEADER.pack(
        _VERSION,
        byte1,
        packet.sequence & 0xFFFF,
        packet.timestamp & 0xFFFFFFFF,
        packet.ssrc & 0xFFFFFFFF,
    )
    return header + packet.payload


def parse(datagram: bytes) -> RtpPacket | None:
    """Parse one RTP datagram, or return None for anything that is not version-2 RTP.

    None rather than raise: a media port on a real network receives strays (STUN probes,
    scanner noise), and a gateway that raised per stray would turn background noise into log
    spam. A None is silently dropped by the caller; nothing downstream sees it.
    """
    if len(datagram) < _HEADER.size:
        return None
    b0, b1, sequence, timestamp, ssrc = _HEADER.unpack_from(datagram)
    if b0 & 0xC0 != _VERSION:
        return None
    csrc_count = b0 & 0x0F
    offset = _HEADER.size + 4 * csrc_count
    if b0 & 0x10:  # header extension present
        if len(datagram) < offset + 4:
            return None
        (ext_words,) = struct.unpack_from("!H", datagram, offset + 2)
        offset += 4 + 4 * ext_words
    if len(datagram) < offset:
        return None
    payload = datagram[offset:]
    if b0 & 0x20 and payload:  # padding: last byte counts the pad
        pad = payload[-1]
        payload = payload[:-pad] if 0 < pad <= len(payload) else b""
    return RtpPacket(
        payload_type=b1 & 0x7F,
        sequence=sequence,
        timestamp=timestamp,
        ssrc=ssrc,
        payload=payload,
        marker=bool(b1 & 0x80),
    )


# --------------------------------------------------------------------------------------- #
# RFC 4733 telephone-event
# --------------------------------------------------------------------------------------- #
_EVENT_DIGITS = "0123456789*#ABCD"


@dataclass(frozen=True, slots=True)
class TelephoneEvent:
    digit: str
    end: bool
    duration: int


def parse_telephone_event(payload: bytes) -> TelephoneEvent | None:
    """Decode one telephone-event payload, or None when it is not one this gateway knows."""
    if len(payload) < 4:
        return None
    event = payload[0]
    if event >= len(_EVENT_DIGITS):
        return None
    return TelephoneEvent(
        digit=_EVENT_DIGITS[event],
        end=bool(payload[1] & 0x80),
        duration=(payload[2] << 8) | payload[3],
    )
