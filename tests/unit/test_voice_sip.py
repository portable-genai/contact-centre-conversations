"""SIP grammar, SDP negotiation and the gateway's fail-closed edges.

The refusals matter most here: the codec this gateway cannot decode is answered 488, a
wildcard peer list refuses to start in every spelling, and a call whose dialled number maps to
no tenant is refused rather than filed under a tenant nobody chose.
"""

from __future__ import annotations

import pytest

from contact_centre_conversations.config import VoiceSettings, build_container
from contact_centre_conversations.voice import sdp, sip
from contact_centre_conversations.voice.gateway import (
    GatewayConfigurationError,
    VoiceGateway,
    parse_peer_allowlist,
    peer_allowed,
)

from tests.conftest import local_settings

_INVITE = (
    b"INVITE sip:6001@bot.example SIP/2.0\r\n"
    b"Via: SIP/2.0/UDP 192.0.2.5:5060;branch=z9hG4bK776asdhds\r\n"
    b"Max-Forwards: 70\r\n"
    b"From: <sip:+6598765432@192.0.2.5>;tag=1928301774\r\n"
    b"To: <sip:6001@bot.example>\r\n"
    b"Call-ID: a84b4c76e66710@192.0.2.5\r\n"
    b"CSeq: 314159 INVITE\r\n"
    b"Contact: <sip:+6598765432@192.0.2.5:5060>\r\n"
    b"X-Contact-Id: contact-sg-0003\r\n"
    b"Content-Type: application/sdp\r\n"
    b"Content-Length: 158\r\n"
    b"\r\n"
    b"v=0\r\n"
    b"o=cube 1 1 IN IP4 192.0.2.5\r\n"
    b"s=call\r\n"
    b"c=IN IP4 192.0.2.5\r\n"
    b"t=0 0\r\n"
    b"m=audio 19240 RTP/AVP 0 101\r\n"
    b"a=rtpmap:0 PCMU/8000\r\n"
    b"a=rtpmap:101 telephone-event/8000\r\n"
    b"a=fmtp:101 0-15\r\n"
)


# --------------------------------------------------------------------------- #
# SIP grammar
# --------------------------------------------------------------------------- #
def test_parse_invite() -> None:
    message = sip.parse(_INVITE)
    assert message is not None and message.is_request
    assert message.method == "INVITE"
    assert message.call_id == "a84b4c76e66710@192.0.2.5"
    assert message.cseq == (314159, "INVITE")
    assert message.header("X-Contact-Id") == "contact-sg-0003"
    assert "m=audio 19240" in message.body


def test_parse_expands_compact_headers() -> None:
    raw = (
        b"BYE sip:a@b SIP/2.0\r\n"
        b"v: SIP/2.0/UDP 192.0.2.5\r\n"
        b"i: short-call-id\r\nf: <sip:a@b>;tag=1\r\nt: <sip:b@b>\r\nCSeq: 2 BYE\r\n\r\n"
    )
    message = sip.parse(raw)
    assert message is not None and message.call_id == "short-call-id"
    assert message.header_values("Via") == ("SIP/2.0/UDP 192.0.2.5",)


def test_parse_refuses_non_sip() -> None:
    assert sip.parse(b"\x80\x01\x02") is None
    assert sip.parse(b"GET / HTTP/1.1\r\n\r\n") is None
    assert sip.parse(b"nonsense") is None


def test_build_response_mirrors_the_transaction_and_tags_once() -> None:
    request = sip.parse(_INVITE)
    assert request is not None
    response = sip.parse(sip.build_response(request, 200, "OK", to_tag="abc", body="x"))
    assert response is not None and response.status == 200
    assert response.header("CSeq") == "314159 INVITE"
    assert response.header("To").endswith(";tag=abc")
    assert response.header("From") == request.header("From")
    # A response to a request whose To already carries a tag must not add a second one.
    tagged = sip.parse(sip.build_response(response_request(request), 200, "OK", to_tag="zzz"))
    assert tagged is not None and tagged.header("To").count(";tag=") == 1


def response_request(request: sip.SipMessage) -> sip.SipMessage:
    headers = tuple((k, f"{v};tag=existing" if k == "To" else v) for k, v in request.headers)
    return sip.SipMessage(method="INVITE", uri=request.uri, headers=headers, body=request.body)


def test_build_request_carries_the_dialog() -> None:
    raw = sip.build_request(
        "REFER",
        "sip:+6598765432@192.0.2.5:5060",
        via_host="127.0.0.1",
        via_port=5060,
        from_header="<sip:6001@bot>;tag=me",
        to_header="<sip:+6598765432@192.0.2.5>;tag=them",
        call_id="a84b4c76e66710@192.0.2.5",
        cseq=2,
        extra_headers=(("Refer-To", "<sip:2000@192.0.2.5>"),),
    )
    message = sip.parse(raw)
    assert message is not None and message.method == "REFER"
    assert message.cseq == (2, "REFER")
    assert message.header("Refer-To") == "<sip:2000@192.0.2.5>"


# --------------------------------------------------------------------------- #
# SDP
# --------------------------------------------------------------------------- #
def test_offer_parses_host_port_and_dtmf() -> None:
    request = sip.parse(_INVITE)
    assert request is not None
    offer = sdp.parse_offer(request.body)
    assert offer.media_host == "192.0.2.5"
    assert offer.media_port == 19240
    assert offer.dtmf_payload_type == 101


def test_offer_without_pcmu_is_refused() -> None:
    body = "v=0\r\nc=IN IP4 1.2.3.4\r\nm=audio 5004 RTP/AVP 8 18\r\na=rtpmap:8 PCMA/8000\r\n"
    with pytest.raises(sdp.UnsupportedOfferError):
        sdp.parse_offer(body)


def test_offer_without_an_address_is_refused() -> None:
    with pytest.raises(sdp.UnsupportedOfferError):
        sdp.parse_offer("v=0\r\nm=audio 5004 RTP/AVP 0\r\n")


def test_a_malformed_offer_is_the_typed_refusal_not_a_bare_valueerror() -> None:
    """A non-numeric port or payload type must surface as UnsupportedOfferError (which the
    gateway turns into 488), not a bare ValueError that escapes as an unhandled task error."""
    with pytest.raises(sdp.UnsupportedOfferError):
        sdp.parse_offer("v=0\r\nc=IN IP4 192.0.2.5\r\nm=audio notaport RTP/AVP 0\r\n")
    with pytest.raises(sdp.UnsupportedOfferError):
        sdp.parse_offer(
            "v=0\r\nc=IN IP4 192.0.2.5\r\nm=audio 5004 RTP/AVP 0 x\r\n"
            "a=rtpmap:x telephone-event/8000\r\n"
        )


def test_answer_names_exactly_one_codec_plus_offered_dtmf() -> None:
    answer = sdp.build_answer(host="127.0.0.1", port=40000, session_id=7, dtmf_payload_type=101)
    assert "m=audio 40000 RTP/AVP 0 101" in answer
    assert "a=rtpmap:0 PCMU/8000" in answer
    assert "a=rtpmap:101 telephone-event/8000" in answer
    without = sdp.build_answer(host="127.0.0.1", port=40000, session_id=7, dtmf_payload_type=None)
    assert "m=audio 40000 RTP/AVP 0\r\n" in without
    assert "telephone-event" not in without


# --------------------------------------------------------------------------- #
# Gateway fail-closed edges
# --------------------------------------------------------------------------- #
def test_unset_peer_allowlist_means_loopback_only() -> None:
    networks = parse_peer_allowlist("")
    assert peer_allowed("127.0.0.1", networks)
    assert peer_allowed("::1", networks)
    assert not peer_allowed("192.0.2.5", networks)


def test_a_configured_allowlist_admits_exactly_what_it_names() -> None:
    networks = parse_peer_allowlist("192.0.2.5, 198.51.100.0/24")
    assert peer_allowed("192.0.2.5", networks)
    assert peer_allowed("198.51.100.77", networks)
    assert not peer_allowed("127.0.0.1", networks)
    assert not peer_allowed("not-an-ip", networks)


@pytest.mark.parametrize("spelling", ["*", "any", "ALL", "0.0.0.0/0", "::/0", "192.0.2.5, *"])
def test_a_wildcard_peer_is_refused_in_every_spelling(spelling: str) -> None:
    with pytest.raises(GatewayConfigurationError):
        parse_peer_allowlist(spelling)


def test_a_malformed_peer_entry_is_refused() -> None:
    with pytest.raises(GatewayConfigurationError):
        parse_peer_allowlist("192.0.2.5, banana")


def _gateway(**voice_overrides: object) -> VoiceGateway:
    settings = local_settings(voice=VoiceSettings(**voice_overrides))  # type: ignore[arg-type]
    return VoiceGateway(build_container(settings), bind_host="127.0.0.1")


def test_contact_identity_honours_a_well_formed_header_and_derives_otherwise() -> None:
    gateway = _gateway()
    invite = sip.parse(_INVITE)
    assert invite is not None
    contact = gateway._contact_for(invite)
    assert contact.contact_id == "contact-sg-0003"  # the trusted header carried a known id
    assert contact.tenant  # the deployment default tenant applied
    hostile = sip.SipMessage(
        method="INVITE",
        uri="sip:6001@bot.example",
        headers=tuple(
            (k, "../../etc/passwd" if k == "X-Contact-Id" else v) for k, v in invite.headers
        ),
        body=invite.body,
    )
    derived = gateway._contact_for(hostile)
    assert derived.contact_id.startswith("voice-"), "a malformed header id must not be honoured"


def test_dnis_routing_wins_over_defaults_and_no_tenant_refuses() -> None:
    gateway = _gateway(dnis={"6001": {"tenant": "other-bank", "market": "HK", "locale": "en-HK"}})
    invite = sip.parse(_INVITE)
    assert invite is not None
    contact = gateway._contact_for(invite)
    assert (contact.tenant, contact.market, contact.locale) == ("other-bank", "HK", "en-HK")

    bare = VoiceGateway(
        build_container(local_settings(tenant="", voice=VoiceSettings())), bind_host="127.0.0.1"
    )
    with pytest.raises(GatewayConfigurationError):
        bare._contact_for(invite)


def test_the_media_plane_pins_the_first_source_and_drops_off_path_packets() -> None:
    """The RTP leg fails closed like the SIP plane: a source not on the peer allowlist is
    dropped, the first legitimate source latches, and a later different source is ignored so it
    cannot hijack the outbound leg or inject caller audio."""
    from contact_centre_conversations.voice import audio, rtp
    from contact_centre_conversations.voice.gateway import ActiveCall, Dialog

    gateway = _gateway(peer_allowlist="192.0.2.5")

    class _Sink:
        def sendto(self, *_a: object, **_k: object) -> None:
            pass

        def close(self) -> None:
            pass

    dialog = Dialog(
        call_id="c1",
        local_tag="t",
        remote_from="<sip:a@b>",
        local_to="<sip:b@b>;tag=t",
        remote_target="sip:a@192.0.2.5",
        peer=("192.0.2.5", 5060),
        invite=sip.parse(_INVITE),  # type: ignore[arg-type]
        answer_sdp="",
    )
    offer = sdp.SdpOffer(media_host="192.0.2.5", media_port=19240, dtmf_payload_type=101)
    call = ActiveCall(
        gateway=gateway, dialog=dialog, offer=offer, rtp_transport=_Sink(), media_port=40000
    )
    heard: list[str] = []

    class _Session:
        async def on_digits(self, dialled: str) -> None:
            heard.append(dialled)

        async def on_caller_audio(self, pcm: bytes, *, sample_rate_hz: int) -> None:
            pass

    call.session = _Session()  # type: ignore[assignment]

    def dtmf_packet(seq: int) -> bytes:
        payload = bytes([1, 0x8A, 0x00, 0xA0])  # digit 1, end flagged
        return rtp.build(
            rtp.RtpPacket(
                payload_type=101, sequence=seq, timestamp=seq * 160, ssrc=1, payload=payload
            )
        )

    # Off-path source (not allowlisted): dropped before it can latch or be processed.
    call.on_rtp(dtmf_packet(1), ("203.0.113.9", 5000))
    assert call._rtp_latched is False
    # First legitimate source latches.
    call.on_rtp(dtmf_packet(2), ("192.0.2.5", 20000))
    assert call._rtp_latched is True
    assert call._rtp_peer == ("192.0.2.5", 20000)
    # A different allowlisted source mid-call is ignored (no re-latch, no injected digit).
    call.on_rtp(dtmf_packet(3), ("192.0.2.5", 20001))
    assert call._rtp_peer == ("192.0.2.5", 20000)
    del audio
