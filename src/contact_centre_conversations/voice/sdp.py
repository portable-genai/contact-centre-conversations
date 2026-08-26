"""SDP offer parsing and answer building for one audio stream, G.711 mu-law only.

The narrowest useful SDP: find the peer's audio address, port and payload types in the offer,
and answer with exactly one codec (PCMU) plus telephone-event when the offer carried it. A
Cisco dial-peer configured ``codec g711ulaw`` and ``dtmf-relay rtp-nte`` produces precisely the
offers this parses, and a softphone offering a longer codec list still lands on PCMU because
answering with one codec is the answerer's prerogative (RFC 3264).

An offer with NO PCMU is refused loudly: the SIP layer turns that into 488 Not Acceptable Here,
which is the truthful answer, not a silently negotiated codec this gateway cannot decode.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The RFC 4733 payload type this gateway answers with when the offer names one; Cisco's
#: convention is 101 and the answer mirrors the OFFERED number, whatever it was.
DEFAULT_DTMF_PT = 101


class UnsupportedOfferError(ValueError):
    """The offer contains no audio stream this gateway can decode (no PCMU)."""


@dataclass(frozen=True, slots=True)
class SdpOffer:
    media_host: str
    media_port: int
    #: The offered payload type for telephone-event, or None when DTMF was not offered.
    dtmf_payload_type: int | None


def parse_offer(body: str) -> SdpOffer:
    session_host = ""
    media_host = ""
    media_port = 0
    offered_pts: list[str] = []
    dtmf_pt: int | None = None
    in_audio = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("c=IN IP4 "):
            host = line.removeprefix("c=IN IP4 ").strip()
            if in_audio:
                media_host = host
            else:
                session_host = host
        elif line.startswith("m="):
            in_audio = line.startswith("m=audio ")
            if in_audio:
                parts = line.split()
                if len(parts) < 4 or not parts[1].isdigit():
                    raise UnsupportedOfferError(f"malformed media line: {line!r}")
                media_port = int(parts[1])
                offered_pts = parts[3:]
        elif in_audio and line.startswith("a=rtpmap:"):
            spec = line.removeprefix("a=rtpmap:")
            pt, _, encoding = spec.partition(" ")
            if encoding.upper().startswith("TELEPHONE-EVENT/8000"):
                if not pt.isdigit():
                    raise UnsupportedOfferError(f"malformed rtpmap line: {line!r}")
                dtmf_pt = int(pt)
    if "0" not in offered_pts:
        raise UnsupportedOfferError(
            "the offer names no G.711 mu-law (payload type 0); this gateway answers PCMU only"
        )
    host = media_host or session_host
    if not host or not media_port:
        raise UnsupportedOfferError("the offer names no audio address and port")
    return SdpOffer(media_host=host, media_port=media_port, dtmf_payload_type=dtmf_pt)


def build_answer(*, host: str, port: int, session_id: int, dtmf_payload_type: int | None) -> str:
    """One audio stream back: PCMU, 20 ms packets, telephone-event iff the offer had it."""
    formats = "0" if dtmf_payload_type is None else f"0 {dtmf_payload_type}"
    lines = [
        "v=0",
        f"o=contact-centre {session_id} {session_id} IN IP4 {host}",
        "s=contact-centre-voice",
        f"c=IN IP4 {host}",
        "t=0 0",
        f"m=audio {port} RTP/AVP {formats}",
        "a=rtpmap:0 PCMU/8000",
    ]
    if dtmf_payload_type is not None:
        lines.append(f"a=rtpmap:{dtmf_payload_type} telephone-event/8000")
        lines.append(f"a=fmtp:{dtmf_payload_type} 0-15")
    lines.append("a=ptime:20")
    lines.append("a=sendrecv")
    return "\r\n".join(lines) + "\r\n"
