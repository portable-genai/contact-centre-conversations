"""SIP message mechanics: parse and build, pure functions, no socket and no timer.

The gateway implements a deliberately narrow UAS surface (what a CUBE dial-peer or a softphone
actually sends at a bot endpoint): OPTIONS, INVITE/ACK/BYE/CANCEL, in-dialog re-INVITE, and an
outbound REFER for the transfer to a human queue. Everything else is answered 501 by the
gateway, which is the honest reply, not a crash.

This module knows the grammar only. Transaction behaviour (answer retransmission until ACK,
dialog state, the REFER-then-BYE dance) lives in ``gateway``, where the sockets are.
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

#: Methods the gateway understands. Anything else gets 501 Not Implemented.
KNOWN_METHODS = frozenset({"INVITE", "ACK", "BYE", "CANCEL", "OPTIONS", "NOTIFY", "REFER", "INFO"})


@dataclass(frozen=True, slots=True)
class SipMessage:
    """One parsed SIP datagram: a request when ``method`` is set, a response otherwise."""

    method: str = ""
    uri: str = ""
    status: int = 0
    reason: str = ""
    headers: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    body: str = ""

    @property
    def is_request(self) -> bool:
        return bool(self.method)

    def header(self, name: str) -> str:
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return ""

    def header_values(self, name: str) -> tuple[str, ...]:
        wanted = name.lower()
        return tuple(value for key, value in self.headers if key.lower() == wanted)

    @property
    def call_id(self) -> str:
        return self.header("Call-ID")

    @property
    def cseq(self) -> tuple[int, str]:
        raw = self.header("CSeq").split()
        if len(raw) == 2 and raw[0].isdigit():
            return int(raw[0]), raw[1].upper()
        return 0, ""


#: Header names expanded from their RFC 3261 compact forms during parsing, so the rest of the
#: gateway never has to remember that ``i:`` is ``Call-ID``.
_COMPACT = {"i": "Call-ID", "m": "Contact", "f": "From", "t": "To", "v": "Via", "c": "Content-Type"}


def parse(datagram: bytes) -> SipMessage | None:
    """Parse one SIP datagram, or None for anything that is not parseable SIP.

    None, not raise: a signalling port on a real network receives strays, and each one is a
    datagram to drop, not a stack trace.
    """
    try:
        text = datagram.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    head, _, body = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines or " " not in lines[0]:
        return None
    start = lines[0]
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if not sep:
            continue
        clean = name.strip()
        headers.append((_COMPACT.get(clean.lower(), clean), value.strip()))
    if start.upper().startswith("SIP/2.0 "):
        parts = start.split(" ", 2)
        if len(parts) < 2 or not parts[1].isdigit():
            return None
        return SipMessage(
            status=int(parts[1]),
            reason=parts[2] if len(parts) > 2 else "",
            headers=tuple(headers),
            body=body,
        )
    parts = start.split(" ")
    if len(parts) != 3 or parts[2].upper() != "SIP/2.0":
        return None
    return SipMessage(
        method=parts[0].upper(),
        uri=parts[1],
        headers=tuple(headers),
        body=body,
    )


def build_response(
    request: SipMessage,
    status: int,
    reason: str,
    *,
    to_tag: str = "",
    contact: str = "",
    extra_headers: tuple[tuple[str, str], ...] = (),
    body: str = "",
    content_type: str = "",
) -> bytes:
    """A response to ``request``: Via, From, Call-ID and CSeq mirrored per RFC 3261."""
    to_value = request.header("To")
    if to_tag and ";tag=" not in to_value:
        to_value = f"{to_value};tag={to_tag}"
    headers: list[tuple[str, str]] = [("Via", via) for via in request.header_values("Via")]
    headers += [
        ("From", request.header("From")),
        ("To", to_value),
        ("Call-ID", request.call_id),
        ("CSeq", request.header("CSeq")),
    ]
    if contact:
        headers.append(("Contact", contact))
    headers.extend(extra_headers)
    return _serialize(f"SIP/2.0 {status} {reason}", headers, body, content_type)


def build_request(
    method: str,
    uri: str,
    *,
    via_host: str,
    via_port: int,
    from_header: str,
    to_header: str,
    call_id: str,
    cseq: int,
    contact: str = "",
    extra_headers: tuple[tuple[str, str], ...] = (),
    body: str = "",
    content_type: str = "",
) -> bytes:
    """An in-dialog request (BYE, REFER, NOTIFY) from this gateway's side of the dialog."""
    headers: list[tuple[str, str]] = [
        ("Via", f"SIP/2.0/UDP {via_host}:{via_port};branch=z9hG4bK{token()};rport"),
        ("From", from_header),
        ("To", to_header),
        ("Call-ID", call_id),
        ("CSeq", f"{cseq} {method.upper()}"),
        ("Max-Forwards", "70"),
    ]
    if contact:
        headers.append(("Contact", contact))
    headers.extend(extra_headers)
    return _serialize(f"{method.upper()} {uri} SIP/2.0", headers, body, content_type)


def token(length: int = 12) -> str:
    """A random token for tags and branches. Uniqueness is what matters, not secrecy."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def _serialize(
    start_line: str,
    headers: list[tuple[str, str]],
    body: str,
    content_type: str,
) -> bytes:
    lines = [start_line]
    lines.extend(f"{name}: {value}" for name, value in headers)
    if content_type:
        lines.append(f"Content-Type: {content_type}")
    lines.append(f"Content-Length: {len(body.encode('utf-8'))}")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode("utf-8")
