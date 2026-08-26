"""The telephony voice gateway: SIP session control, RTP media, and the call orchestrator.

This package is the application-layer edge that lets an existing enterprise telephone estate
(a Cisco CUBE dial-peer trunk, or any SIP peer, including a desk softphone) reach the SAME
deterministic self-service pipeline the chat surface reaches. It is reference-grade on purpose:
G.711 over plain RTP, UDP SIP, one process, zero extra infrastructure, so a company can test it
against a laptop softphone today and their CUBE tomorrow, and put a production SBC in front
when they promote it (docs/cisco-connection-guide.md walks both).

Layering, which the tests enforce by construction:

* ``audio``, ``rtp``, ``sdp``, ``sip``, ``dtmf`` are pure protocol mechanics over the standard
  library: parse, build, transcode. No sockets, no clocks of their own, fully unit-testable.
* ``session`` is the per-call orchestrator: engine events in, deterministic decisions through
  :class:`~..domain.self_service.SelfServiceService`, speech and transfers out. It knows
  nothing about SIP or sockets.
* ``gateway`` owns the sockets and the SIP state machine, and is the only module here that
  binds a port on an interface, which is why it is also where the fail-closed exposure rules
  (loopback by default, peer allowlist, wildcard refused) live.

Audio never enters ``domain/``: by the time anything downstream of this package sees a
contact, it is turns of text, redacted first, exactly as the chat path delivers them.
"""
