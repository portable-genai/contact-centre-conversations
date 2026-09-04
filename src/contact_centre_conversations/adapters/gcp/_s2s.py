"""Service-to-service transport shared by the platform-remote adapters in this family.

Three of E1's managed adapters are not cloud SDK clients at all: the agent-guardrail-gateway screen,
the enterprise-knowledge-base governed-RAG retrieval and the MCP action catalog are HTTP calls to
sibling services. They ride the same S2S rules every other producer uses, sourced from the commons
rather than restated: an ``https://`` base URL outside loopback, a bearer token, and an optional
HMAC-signed end-user actor.

Every read here resolves THREE states, and that is the commons' own work rather than this
module's. A header builder that takes env var NAMES and strips a value before it tests it
collapses UNSET and SET-AND-EMPTY into ONE state: neither attaches an ``Authorization`` header,
so an operator who deliberately emptied ``S2S_TOKEN`` gets exactly what an operator who
never set it gets, silently. ``client_headers`` raises ``ConfiguredEmptyError`` for an emptied
credential instead, which is why this module does not resolve the names itself: a rule kept in
two places drifts in two places, and a local copy reading through the very same
``read_env_setting`` the commons uses would be a second copy rather than an independent
check.

Unset is not a member of the valid set for the bearer either, and that rule is NOT the commons'
default, so it is still expressed here: a sibling reached over a non-loopback URL is a real
service with a real credential requirement, and ``require_token=`` makes an absent token refuse
BEFORE the request leaves rather than surface as a 401 at the far end. The carve-out is the one
``validate_base_url`` already makes: a loopback sibling is the offline zero-secret posture and
needs no bearer. This matches ``review_kit.client``, which requires the outbound R8 bearer
the same way, so the two outbound pairs this service holds fail alike.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from hex_service_kit.netdefaults import is_loopback_host
from hex_service_kit.s2s import client_headers, validate_base_url

#: Env var holding the bearer credential for outbound S2S calls. Required for a non-loopback
#: sibling, refused when emptied, and never inherited from the unset state.
TOKEN_ENV = "S2S_TOKEN"
#: Env var holding the HMAC key for signing the propagated end-user actor. Optional when unset
#: (the pair is omitted rather than sent unsigned), refused when emptied.
SIGNING_KEY_ENV = "S2S_SIGNING_KEY"
_ACTOR_HEADER = "X-Cc-Actor"
_ACTOR_SIG_HEADER = "X-Cc-Actor-Sig"

#: Every remote call is bounded. An adapter that can hang is an adapter that takes a contact
#: centre down one live conversation at a time.
DEFAULT_TIMEOUT_SECONDS = 5.0

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "SIGNING_KEY_ENV",
    "TOKEN_ENV",
    "headers",
    "post_json",
    "require_base_url",
]


def headers(base_url: str, actor: str = "") -> dict[str, str]:
    """Auth headers for one S2S request (bearer token plus optional signed actor).

    ``base_url`` is what decides whether the bearer is REQUIRED, on the same loopback carve-out
    :func:`require_base_url` already applies to the scheme, and that decision is the only part
    of the credential policy this module still owns. The commons resolves both names in three
    states itself, refusing an emptied one wherever the sibling is; ``require_token`` extends
    that to the UNSET bearer for a real sibling, which is not the commons' default because a
    loopback caller legitimately has none. Assembling the headers here instead would fork the
    HMAC and the header casing away from the verifier the siblings run
    (``hex_service_kit.web.make_require_service_caller``).
    """
    return client_headers(
        actor,
        token_env=TOKEN_ENV,
        signing_key_env=SIGNING_KEY_ENV,
        actor_header=_ACTOR_HEADER,
        actor_sig_header=_ACTOR_SIG_HEADER,
        require_token=not is_loopback_host(urlparse(base_url).hostname),
    )


def require_base_url(value: str, *, what: str) -> str:
    """Validate a configured base URL, refusing an empty one rather than defaulting."""
    base = value.strip()
    if not base:
        raise RuntimeError(
            f"{what} is not configured. An unconfigured remote dependency is not an absent "
            "requirement: set it in config/settings.yaml, or bind a different adapter family."
        )
    return validate_base_url(base, service=what).rstrip("/")


def post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    actor: str = "",
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST one JSON document and return the parsed response, or raise.

    Raising is the contract. Every caller in this family turns a failure into a fail-closed
    verdict (an unavailable screen, an unreachable index), and a function that returned an empty
    dict on error would hand each of them a plausible-looking success instead. An unusable
    credential raises from :func:`headers` here, before the socket is opened.
    """
    request = urllib.request.Request(  # noqa: S310 - scheme validated by require_base_url
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", **headers(base_url, actor)},
    )
    with urllib.request.urlopen(  # noqa: S310 - scheme validated by require_base_url
        request, timeout=timeout or DEFAULT_TIMEOUT_SECONDS
    ) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError(f"{base_url}{path} returned a {type(parsed).__name__}, not an object")
    return parsed
