"""The SIP/RTP gateway process: answer the trunk, run the session, transfer to people.

One asyncio process owns a UDP SIP socket and one UDP RTP socket per active call. A Cisco CUBE
dial-peer, any other SIP peer, or a desk softphone INVITEs it directly; production deployments
put an SBC in front (docs/cisco-connection-guide.md) and nothing here changes.

Exposure rules, the same fail-closed shape as the HTTP surface and enforced BEFORE any socket
binds:

* the bind host comes from the commons' ``resolve_bind_host``: the local profile stays on
  loopback unless the LAN demo variable is deliberately set, and only a fronted profile may
  take every interface;
* signalling is accepted only from the configured peer allowlist. Unset means loopback peers
  only; a deliberately EMPTIED allowlist refuses to start (three states, as everywhere); a
  wildcard is refused in every spelling, because "any host may send us calls" is not a peer
  list, it is the absence of one;
* the self-service mode gate is checked at startup AND per call: a mode nobody enabled cannot
  be reached by telephone any more than by HTTP.

The caller is a member of the public on the PSTN: there is no end-user credential to verify,
and nothing here pretends otherwise. Identity of the PEER (the trunk) is the allowlist; the
caller's ANI and DNIS are DATA on the audit trail, never an authentication.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import time
from collections import deque
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any

from hex_service_kit.netdefaults import resolve_bind_host

from .. import services
from ..config import PROFILE_CHOICE, Container
from ..domain.models import ContactChannel, ContactRef
from ..domain.modes import ContactMode
from . import audio, rtp, sdp, sip
from .dtmf import DigitCollector
from .session import VoiceCallSession

_ALLOW = "INVITE, ACK, BYE, CANCEL, OPTIONS, NOTIFY, INFO"
_CONTACT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_USER_PATTERN = re.compile(r"sip:([^@;>]+)")

#: How long after an accepted REFER the gateway waits for the peer's BYE before hanging up
#: itself. A transfer that nobody completes must not hold the line open forever.
_TRANSFER_GRACE_S = 15.0


class GatewayConfigurationError(RuntimeError):
    """The gateway refused to start; the message names the setting to fix."""


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


# --------------------------------------------------------------------------------------- #
# Peer allowlist
# --------------------------------------------------------------------------------------- #
_WILDCARDS = frozenset({"*", "any", "all", "0.0.0.0", "0.0.0.0/0", "::", "::/0"})


def parse_peer_allowlist(raw: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """The signalling peers this gateway will talk to. Empty input means LOOPBACK ONLY.

    A wildcard is refused in every spelling: a gateway that accepts INVITEs from anywhere is a
    toll-fraud endpoint by lunchtime, and no deployment chooses that by typing an asterisk.
    """
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    if not entries:
        return (
            ipaddress.ip_network("127.0.0.0/8"),
            ipaddress.ip_network("::1/128"),
        )
    networks = []
    for entry in entries:
        if entry.lower() in _WILDCARDS:
            raise GatewayConfigurationError(
                f"peer allowlist entry {entry!r} is a wildcard; list the trunk addresses "
                "(CONTACT_SIP_PEER_ALLOWLIST takes IPs or CIDR ranges, comma separated)"
            )
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise GatewayConfigurationError(
                f"peer allowlist entry {entry!r} is not an IP address or CIDR range"
            ) from exc
    return tuple(networks)


def peer_allowed(
    host: str, networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]
) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in networks)


# --------------------------------------------------------------------------------------- #
# Per-call state
# --------------------------------------------------------------------------------------- #
@dataclass
class Dialog:
    """The SIP dialog view of one call, from this gateway's side."""

    call_id: str
    local_tag: str
    remote_from: str
    local_to: str
    remote_target: str
    peer: tuple[str, int]
    invite: sip.SipMessage
    answer_sdp: str
    cseq: int = 1

    def next_cseq(self) -> int:
        self.cseq += 1
        return self.cseq


class ActiveCall:
    """One answered call: the RTP leg, the DTMF collector and the engine session."""

    def __init__(
        self,
        *,
        gateway: VoiceGateway,
        dialog: Dialog,
        offer: sdp.SdpOffer,
        rtp_transport: asyncio.DatagramTransport,
        media_port: int,
    ) -> None:
        self.gateway = gateway
        self.dialog = dialog
        self.offer = offer
        self.rtp_transport = rtp_transport
        self.media_port = media_port
        self.session: VoiceCallSession | None = None
        #: Set by the gateway between _answer and ACK; resolved at INVITE time, fail-closed.
        self.contact: ContactRef | None = None
        self.digits = DigitCollector(
            terminator=gateway.container.settings.voice.dtmf_terminator,
            inter_digit_timeout_ms=gateway.container.settings.voice.dtmf_timeout_ms,
        )
        self._playout: deque[bytes] = deque()
        self._rtp_peer: tuple[str, int] = (offer.media_host, offer.media_port)
        #: Symmetric RTP latches ONCE, to the first inbound source, then pins. Re-latching on
        #: every packet let any host that found the media port hijack the outbound leg and
        #: inject caller audio or DTMF; a media source is also required to be on the same peer
        #: allowlist the SIP plane enforces, so an off-path host is dropped before it can latch.
        self._rtp_latched = False
        self._sequence = 0
        self._timestamp = 0
        self._ssrc = int.from_bytes(sip.token(4).encode("ascii"), "big") & 0x7FFFFFFF
        self._tasks: list[asyncio.Task[None]] = []
        self._transfer_requested = False
        self._closed = False

    # ------------------------------------------------------------------ transport surface
    def play_pcm(self, pcm: bytes, sample_rate_hz: int) -> None:
        if sample_rate_hz == 24_000:
            pcm8k = audio.downsample_3x(pcm)
        elif sample_rate_hz == 16_000:
            pcm8k = audio.downsample_2x(pcm)
        elif sample_rate_hz == 8_000:
            pcm8k = pcm
        else:
            raise ValueError(f"unsupported playout rate {sample_rate_hz} Hz")
        ulaw = audio.pcm_to_ulaw(pcm8k)
        for start in range(0, len(ulaw), audio.SAMPLES_PER_FRAME):
            frame = ulaw[start : start + audio.SAMPLES_PER_FRAME]
            if len(frame) < audio.SAMPLES_PER_FRAME:
                frame = frame + audio.SILENCE_ULAW_FRAME[len(frame) :]
            self._playout.append(frame)

    def flush_playout(self) -> None:
        self._playout.clear()

    def request_transfer(self, reason: str) -> None:
        if not self._transfer_requested:
            self._transfer_requested = True
            self._spawn(self.gateway.transfer(self, reason))

    def hangup(self) -> None:
        self._spawn(self.gateway.end_call(self, send_bye=True))

    # ------------------------------------------------------------------ media
    def on_rtp(self, datagram: bytes, addr: tuple[str, int]) -> None:
        # The media plane fails closed like the signalling plane: a source that is not on the
        # peer allowlist is dropped before it can be parsed, latched or fed to the pipeline.
        if not peer_allowed(addr[0], self.gateway.peers):
            return
        packet = rtp.parse(datagram)
        if packet is None:
            return
        if not self._rtp_latched:
            # Latch ONCE to the first legitimate source (symmetric RTP: the peer's real send
            # address may differ from the SDP under NAT), then pin. A later source change is a
            # hijack attempt, not a NAT rebind we will chase.
            self._rtp_peer = addr
            self._rtp_latched = True
        elif addr != self._rtp_peer:
            return
        if self.session is None:
            return
        if self.offer.dtmf_payload_type is not None and (
            packet.payload_type == self.offer.dtmf_payload_type
        ):
            event = rtp.parse_telephone_event(packet.payload)
            if event is not None:
                dialled = self.digits.on_event(event, now_ms=_now_ms())
                if dialled:
                    self._spawn(self.session.on_digits(dialled))
        elif packet.payload_type == rtp.PT_PCMU and packet.payload:
            pcm16k = audio.upsample_2x(audio.ulaw_to_pcm(packet.payload))
            self._spawn(self.session.on_caller_audio(pcm16k, sample_rate_hz=16_000))

    async def pace_playout(self) -> None:
        """Send one frame every 20 ms: queued speech when there is some, silence otherwise.

        The silence is not decoration. CUBE's media-inactivity detection watches the RTP it
        RECEIVES, and an engine reconnect (a Live ``goAway``) can leave seconds with nothing to
        say; a leg that goes quiet gets torn down as dead. Continuous frames keep the call.
        """
        frame_s = audio.FRAME_MS / 1000
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while not self._closed:
            frame = self._playout.popleft() if self._playout else audio.SILENCE_ULAW_FRAME
            self._sequence = (self._sequence + 1) & 0xFFFF
            self._timestamp = (self._timestamp + audio.SAMPLES_PER_FRAME) & 0xFFFFFFFF
            packet = rtp.RtpPacket(
                payload_type=rtp.PT_PCMU,
                sequence=self._sequence,
                timestamp=self._timestamp,
                ssrc=self._ssrc,
                payload=frame,
            )
            self.rtp_transport.sendto(rtp.build(packet), self._rtp_peer)
            if self.session is not None:
                dialled = self.digits.on_tick(now_ms=_now_ms())
                if dialled:
                    self._spawn(self.session.on_digits(dialled))
            # Absolute-deadline pacing: sleep-after-work would add the work and the scheduler
            # jitter to every period and the stream would slip behind realtime by percents,
            # which a long synthesis playout turns into an audible, growing lag.
            deadline += frame_s
            delay = deadline - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                deadline = loop.time()  # fell behind (suspended laptop): resynchronise

    # ------------------------------------------------------------------ lifecycle
    def start(self, session: VoiceCallSession) -> None:
        self.session = session
        self._spawn(self._run(session))
        self._spawn(self.pace_playout())

    async def _run(self, session: VoiceCallSession) -> None:
        try:
            await session.start()
            await session.run()
        except Exception:  # noqa: BLE001 - a dead session must still end the CALL cleanly
            await self.gateway.end_call(self, send_bye=True)
            return
        # The engine's event stream ended (and any failure line has been spoken): the call is
        # over from this side. A leg left up with a silent bot is an outage shaped like hold.
        await self.gateway.end_call(self, send_bye=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.session is not None:
            with contextlib.suppress(Exception):
                await self.session.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self.rtp_transport.close()

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)
        self._tasks = [t for t in self._tasks if not t.done()]


class _RtpProtocol(asyncio.DatagramProtocol):
    def __init__(self, call_ref: list[ActiveCall]) -> None:
        self._call_ref = call_ref

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._call_ref:
            self._call_ref[0].on_rtp(data, addr)


# --------------------------------------------------------------------------------------- #
# The gateway
# --------------------------------------------------------------------------------------- #
class VoiceGateway(asyncio.DatagramProtocol):
    """The SIP UAS plus the call table. One instance per process."""

    def __init__(self, container: Container, *, bind_host: str) -> None:
        self.container = container
        self.bind_host = bind_host
        self.peers = parse_peer_allowlist(container.settings.voice.peer_allowlist)
        self.calls: dict[str, ActiveCall] = {}
        self._pending: dict[str, tuple[sip.SipMessage, bytes, tuple[str, int]]] = {}
        #: One reaper task per un-ACKed 200 OK: it retransmits the answer and, if no ACK ever
        #: arrives, reclaims the dialog, the RTP port and the calls entry rather than leaking
        #: them for the life of the process.
        self._ack_reapers: dict[str, asyncio.Task[None]] = {}
        self._services = services.build_services(container)
        self._transport: asyncio.DatagramTransport | None = None

    # ------------------------------------------------------------------ socket plumbing
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not peer_allowed(addr[0], self.peers):
            return  # not a peer: no reply at all, an unlisted host learns nothing
        message = sip.parse(data)
        if message is None or not message.is_request:
            return
        asyncio.ensure_future(self._dispatch(message, addr))

    def _send(self, payload: bytes, addr: tuple[str, int]) -> None:
        if self._transport is not None:
            self._transport.sendto(payload, addr)

    # ------------------------------------------------------------------ SIP dispatch
    async def _dispatch(self, message: sip.SipMessage, addr: tuple[str, int]) -> None:
        method = message.method
        if method == "OPTIONS":
            self._send(
                sip.build_response(message, 200, "OK", extra_headers=(("Allow", _ALLOW),)), addr
            )
        elif method == "INVITE":
            await self._on_invite(message, addr)
        elif method == "ACK":
            await self._on_ack(message)
        elif method == "CANCEL":
            self._on_cancel(message, addr)
        elif method == "BYE":
            await self._on_bye(message, addr)
        elif method in {"NOTIFY", "INFO"}:
            self._send(sip.build_response(message, 200, "OK"), addr)
        else:
            self._send(sip.build_response(message, 501, "Not Implemented"), addr)

    async def _on_invite(self, message: sip.SipMessage, addr: tuple[str, int]) -> None:
        call_id = message.call_id
        active = self.calls.get(call_id)
        if active is not None:
            # In-dialog re-INVITE (session refresh or hold): same answer, same session.
            response = sip.build_response(
                message,
                200,
                "OK",
                to_tag=active.dialog.local_tag,
                contact=self._contact_header(),
                body=active.dialog.answer_sdp,
                content_type="application/sdp",
            )
            self._send(response, addr)
            return
        if call_id in self._pending:
            _, response, _ = self._pending[call_id]
            self._send(response, addr)  # retransmitted INVITE: retransmit the answer
            return
        try:
            services.require_mode(self.container, ContactMode.SELF_SERVICE)
        except Exception:  # noqa: BLE001 - any gate refusal is the same 503 to the trunk
            self._send(sip.build_response(message, 503, "Service Unavailable"), addr)
            return
        try:
            offer = sdp.parse_offer(message.body)
        except sdp.UnsupportedOfferError:
            self._send(sip.build_response(message, 488, "Not Acceptable Here"), addr)
            return
        try:
            # Resolved BEFORE answering: a call this deployment cannot serve (no tenant for
            # the dialled number) is refused while refusing is still cheap, not discovered
            # at ACK time on a call the caller already believes is answered.
            contact = self._contact_for(message)
        except GatewayConfigurationError:
            self._send(sip.build_response(message, 503, "Service Unavailable"), addr)
            return
        self._send(sip.build_response(message, 100, "Trying"), addr)
        try:
            call = await self._answer(message, offer, addr, contact)
        except Exception:  # noqa: BLE001 - an unanswerable call is refused, not dropped
            self._send(sip.build_response(message, 503, "Service Unavailable"), addr)
            return
        response = sip.build_response(
            message,
            200,
            "OK",
            to_tag=call.dialog.local_tag,
            contact=self._contact_header(),
            extra_headers=(("Allow", _ALLOW),),
            body=call.dialog.answer_sdp,
            content_type="application/sdp",
        )
        self._pending[call_id] = (message, response, addr)
        self._send(response, addr)
        self._ack_reapers[call_id] = asyncio.ensure_future(self._await_ack(call_id, response, addr))

    async def _await_ack(self, call_id: str, response: bytes, addr: tuple[str, int]) -> None:
        """Retransmit the 2xx on the RFC 3261 timer; reclaim the call if the ACK never comes.

        A UAS 2xx is retransmitted by the UAS itself (the transaction layer does not, once the
        INVITE transaction completes), so a single lost 200 OK otherwise strands the dialog and
        leaks its RTP port forever. The retransmits stop on ACK (``_on_ack`` cancels this task)
        and the whole call is torn down at the 32 s ceiling.
        """
        delay = 0.5
        waited = 0.0
        try:
            while waited < 32.0:
                await asyncio.sleep(delay)
                waited += delay
                if call_id not in self._pending:
                    return  # ACK arrived; _on_ack removed the pending entry and cancelled us
                self._send(response, addr)
                delay = min(delay * 2, 4.0)
            self._pending.pop(call_id, None)
            call = self.calls.get(call_id)
            if call is not None:
                await self.end_call(call, send_bye=False)
        except asyncio.CancelledError:
            raise

    def _cancel_ack_reaper(self, call_id: str) -> None:
        reaper = self._ack_reapers.pop(call_id, None)
        if reaper is not None:
            reaper.cancel()

    async def _answer(
        self,
        message: sip.SipMessage,
        offer: sdp.SdpOffer,
        addr: tuple[str, int],
        contact: ContactRef,
    ) -> ActiveCall:
        loop = asyncio.get_running_loop()
        call_ref: list[ActiveCall] = []
        media_port, transport = await self._bind_media(loop, call_ref)
        local_tag = sip.token()
        answer = sdp.build_answer(
            host=self._media_host(addr),
            port=media_port,
            session_id=_now_ms() & 0x7FFFFFFF,
            dtmf_payload_type=offer.dtmf_payload_type,
        )
        contact_header = message.header("Contact")
        remote_target = _uri_of(contact_header) or f"sip:{addr[0]}:{addr[1]}"
        dialog = Dialog(
            call_id=message.call_id,
            local_tag=local_tag,
            remote_from=message.header("From"),
            local_to=f"{message.header('To')};tag={local_tag}",
            remote_target=remote_target,
            peer=addr,
            invite=message,
            answer_sdp=answer,
        )
        call = ActiveCall(
            gateway=self,
            dialog=dialog,
            offer=offer,
            rtp_transport=transport,
            media_port=media_port,
        )
        call.contact = contact
        call_ref.append(call)
        self.calls[message.call_id] = call
        return call

    async def _on_ack(self, message: sip.SipMessage) -> None:
        self._cancel_ack_reaper(message.call_id)
        pending = self._pending.pop(message.call_id, None)
        call = self.calls.get(message.call_id)
        if pending is None or call is None or call.session is not None:
            return
        try:
            if call.contact is None:
                raise GatewayConfigurationError("the call has no resolved contact")
            session = VoiceCallSession(
                settings=self.container.settings,
                contact=call.contact,
                engine=self.container.voice_engine,
                service=self._services.self_service,
                store=self.container.contact_store,
                tools=self.container.tool_catalog,
                transport=call,
            )
            call.start(session)
        except Exception:  # noqa: BLE001 - a call that cannot start must END, not zombify
            await self.end_call(call, send_bye=True)

    def _on_cancel(self, message: sip.SipMessage, addr: tuple[str, int]) -> None:
        self._send(sip.build_response(message, 200, "OK"), addr)
        self._cancel_ack_reaper(message.call_id)
        pending = self._pending.pop(message.call_id, None)
        if pending is not None:
            invite, _, invite_addr = pending
            self._send(sip.build_response(invite, 487, "Request Terminated"), invite_addr)
        call = self.calls.pop(message.call_id, None)
        if call is not None:
            asyncio.ensure_future(call.close())

    async def _on_bye(self, message: sip.SipMessage, addr: tuple[str, int]) -> None:
        self._send(sip.build_response(message, 200, "OK"), addr)
        self._cancel_ack_reaper(message.call_id)
        call = self.calls.pop(message.call_id, None)
        self._pending.pop(message.call_id, None)
        if call is not None:
            await call.close()

    # ------------------------------------------------------------------ outbound dialog acts
    async def transfer(self, call: ActiveCall, reason: str) -> None:
        """REFER the peer toward the human queue, then give it a moment to take the call away.

        CUBE consumes the REFER and re-routes by dial-peer (the Refer-To host is not what
        routes; the digits are), then sends BYE to this leg. If nothing happens inside the
        grace period the gateway hangs up rather than holding a caller in limbo.
        """
        target = self.container.settings.voice.transfer_target
        if not target:
            # Nowhere to send them: the session has already spoken; end the call honestly.
            await self.end_call(call, send_bye=True)
            return
        refer_to = target if target.startswith("sip:") else f"sip:{target}@{call.dialog.peer[0]}"
        dialog = call.dialog
        request = sip.build_request(
            "REFER",
            dialog.remote_target,
            via_host=self.bind_host,
            via_port=self.container.settings.voice.sip_port,
            from_header=dialog.local_to,
            to_header=dialog.remote_from,
            call_id=dialog.call_id,
            cseq=dialog.next_cseq(),
            contact=self._contact_header(),
            extra_headers=(
                ("Refer-To", f"<{refer_to}>"),
                ("Referred-By", self._contact_header()),
                ("X-Handoff-Reason", reason),
            ),
        )
        self._send(request, dialog.peer)
        await asyncio.sleep(_TRANSFER_GRACE_S)
        if dialog.call_id in self.calls:
            await self.end_call(call, send_bye=True)

    async def end_call(self, call: ActiveCall, *, send_bye: bool) -> None:
        dialog = call.dialog
        if self.calls.pop(dialog.call_id, None) is None:
            return
        self._cancel_ack_reaper(dialog.call_id)
        self._pending.pop(dialog.call_id, None)
        if send_bye:
            request = sip.build_request(
                "BYE",
                dialog.remote_target,
                via_host=self.bind_host,
                via_port=self.container.settings.voice.sip_port,
                from_header=dialog.local_to,
                to_header=dialog.remote_from,
                call_id=dialog.call_id,
                cseq=dialog.next_cseq(),
            )
            self._send(request, dialog.peer)
        await call.close()

    # ------------------------------------------------------------------ call identity
    def _contact_for(self, invite: sip.SipMessage) -> ContactRef:
        settings = self.container.settings
        dnis = _user_of(invite.uri) or _user_of(invite.header("To"))
        # The From user (ANI) is deliberately NOT read into an identity: a caller-supplied
        # number authenticates nobody. It reaches the audit trail through the transcript.
        route = settings.voice.dnis.get(dnis, {})
        tenant = str(route.get("tenant", "") or settings.tenant)
        if not tenant:
            raise GatewayConfigurationError(
                f"no tenant is mapped for DNIS {dnis!r} and no default tenant is configured; "
                "map it under voice.dnis in config/settings.yaml or set CONTACT_TENANT"
            )
        header_id = invite.header(settings.voice.contact_header)
        if header_id and _CONTACT_ID_PATTERN.match(header_id):
            contact_id = header_id
        else:
            contact_id = f"voice-{re.sub(r'[^A-Za-z0-9._-]', '', invite.call_id)[:32] or 'call'}"
        return ContactRef(
            contact_id=contact_id,
            tenant=tenant,
            market=str(route.get("market", "") or settings.voice.default_market),
            locale=str(route.get("locale", "") or settings.voice.default_locale),
            mode=ContactMode.SELF_SERVICE,
            channel=ContactChannel.VOICE,
        )

    # ------------------------------------------------------------------ misc plumbing
    def _contact_header(self) -> str:
        return f"<sip:assistant@{self.bind_host}:{self.container.settings.voice.sip_port}>"

    def _media_host(self, peer: tuple[str, int]) -> str:
        if self.bind_host not in {"0.0.0.0", "::"}:
            return self.bind_host
        # Bound on every interface: advertise the interface that routes toward the peer.
        import socket  # noqa: PLC0415 - stdlib, deferred to keep module import trivial

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(peer)
            return str(probe.getsockname()[0])
        finally:
            probe.close()

    async def _bind_media(
        self, loop: asyncio.AbstractEventLoop, call_ref: list[ActiveCall]
    ) -> tuple[int, asyncio.DatagramTransport]:
        settings = self.container.settings.voice
        last_error: Exception | None = None
        for port in range(settings.rtp_port_min, settings.rtp_port_max + 1, 2):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _RtpProtocol(call_ref), local_addr=(self.bind_host, port)
                )
                return port, transport
            except OSError as exc:
                last_error = exc
        raise GatewayConfigurationError(
            f"no free RTP port in {settings.rtp_port_min}..{settings.rtp_port_max}"
        ) from last_error


def _user_of(value: str) -> str:
    match = _USER_PATTERN.search(value)
    return match.group(1) if match else ""


def _uri_of(contact: str) -> str:
    start = contact.find("<")
    end = contact.find(">")
    if start != -1 and end > start:
        return contact[start + 1 : end]
    return contact.strip()


# --------------------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GatewayHandle:
    gateway: VoiceGateway
    transport: asyncio.DatagramTransport = field(repr=False)


async def start_gateway(container: Container) -> GatewayHandle:
    """Bind the SIP socket and return. Refuses before binding when the posture is wrong."""
    services.require_mode(container, ContactMode.SELF_SERVICE)
    bind_host = resolve_bind_host(
        PROFILE_CHOICE.bind_profile,
        host_env="CONTACT_VOICE_HOST",
        insecure_demo_env="CONTACT_VOICE_LAN_DEMO",
    )
    gateway = VoiceGateway(container, bind_host=bind_host)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: gateway, local_addr=(bind_host, container.settings.voice.sip_port)
    )
    return GatewayHandle(gateway=gateway, transport=transport)


async def serve_forever(container: Container) -> None:
    handle = await start_gateway(container)
    try:
        await asyncio.Event().wait()
    finally:
        for call in list(handle.gateway.calls.values()):
            await handle.gateway.end_call(call, send_bye=True)
        handle.transport.close()
