"""The outbound S2S pair resolves THREE states, and refuses two of them before the call leaves.

`adapters/gcp/_s2s.py` carries the credentials the Hrz1 guardrail screen (rule R1), the Hrz2
governed index (rule R3) and the MCP action catalog present to their siblings. Both were read by
`hex_service_kit.s2s.client_headers`, which stripped a value before it tested it, so UNSET and
SET-AND-EMPTY were ONE state: an operator who deliberately emptied `S2S_TOKEN` got exactly
what an operator who never set it got, which is a call leaving with no `Authorization` header on
it at all. Nothing in this process refused, and the symptom was a 401 at the far end.

The three states are held apart, and the table is unchanged by where the rule now lives:

* SET-AND-EMPTY is refused for BOTH credentials, on a loopback sibling too. A blank value is one
  somebody believes they configured. This is the commons' own rule.
* UNSET is refused for the BEARER when the sibling is not on loopback, matching what
  `review_kit.client` already does for the outbound rule-R8 pair, so this service's two
  outbound pairs fail alike rather than one of them being quietly weaker. This one is NOT the
  commons' default, so `headers()` asks for it with `require_token=`.
* UNSET is accepted for the SIGNING KEY, which is the documented "not in use" state: the
  signed-actor pair is omitted rather than sent unsigned.

The module does not resolve either name itself: a local copy would read through the very
`read_env_setting` the commons uses, so it would duplicate the rule rather than check it
independently. These cases are written against `_s2s.headers` rather than against whichever
layer enforces them, so they hold whichever layer that is.

`tests/unit/test_three_state_env_reads.py` guards the SHAPE (a name may not be handed to a
two-state reader unresolved); this file guards the BEHAVIOUR.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest
from hex_service_kit.netdefaults import ConfiguredEmptyError
from hex_service_kit.s2s import client_headers

from contact_centre_conversations.adapters.gcp import _s2s

_REMOTE = "https://guardrail.example"
_LOOPBACK = "http://localhost:8081"
_ACTOR = "agent-4471@contact.example"

# Asserted as literals, not read back from the module: these two names are a WIRE contract with
# the siblings' verifier, so a rename has to break a test rather than follow the constant.
_ACTOR_HEADER = "X-Cc-Actor"
_ACTOR_SIG_HEADER = "X-Cc-Actor-Sig"


@pytest.fixture(autouse=True)
def _no_inherited_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case from the unset state, whatever the developer's shell holds."""
    monkeypatch.delenv(_s2s.TOKEN_ENV, raising=False)
    monkeypatch.delenv(_s2s.SIGNING_KEY_ENV, raising=False)


# --------------------------------------------------------------------------- #
# The bearer: unset is not a member of the valid set for a real sibling
# --------------------------------------------------------------------------- #
def test_a_non_loopback_sibling_refuses_an_unset_bearer() -> None:
    with pytest.raises(ValueError, match=_s2s.TOKEN_ENV) as raised:
        _s2s.headers(_REMOTE)
    assert "not consent to call unauthenticated" in str(raised.value)


def test_a_loopback_sibling_is_the_zero_secret_offline_posture() -> None:
    """The carve-out `validate_base_url` already makes for the scheme, made once more here."""
    assert _s2s.headers(_LOOPBACK) == {}


@pytest.mark.parametrize("base_url", [_REMOTE, _LOOPBACK])
@pytest.mark.parametrize("blank", ["", "   "])
def test_an_emptied_bearer_is_refused_wherever_the_sibling_is(
    monkeypatch: pytest.MonkeyPatch, base_url: str, blank: str
) -> None:
    """Whitespace included: `strip()` is what would fold this state into the unset one."""
    monkeypatch.setenv(_s2s.TOKEN_ENV, blank)
    with pytest.raises(ConfiguredEmptyError, match=_s2s.TOKEN_ENV) as raised:
        _s2s.headers(base_url)
    assert "never inherits the unset default" in str(raised.value)


def test_a_configured_bearer_is_attached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "fictional-sibling-token")
    assert _s2s.headers(_REMOTE) == {"Authorization": "Bearer fictional-sibling-token"}


# --------------------------------------------------------------------------- #
# The signing key: unset means "not in use", emptied still means "misconfigured"
# --------------------------------------------------------------------------- #
def test_an_unset_signing_key_omits_the_actor_pair_rather_than_sending_it_unsigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "fictional-sibling-token")
    out = _s2s.headers(_REMOTE, _ACTOR)
    assert _ACTOR_HEADER not in out
    assert _ACTOR_SIG_HEADER not in out


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_emptied_signing_key_is_refused_although_an_unset_one_is_not(
    monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """The asymmetry is deliberate: absent is a posture, blank is a mistake."""
    monkeypatch.setenv(_s2s.TOKEN_ENV, "fictional-sibling-token")
    monkeypatch.setenv(_s2s.SIGNING_KEY_ENV, blank)
    with pytest.raises(ConfiguredEmptyError, match=_s2s.SIGNING_KEY_ENV):
        _s2s.headers(_REMOTE, _ACTOR)


def test_a_configured_signing_key_signs_the_actor_on_this_services_header_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_s2s.TOKEN_ENV, "fictional-sibling-token")
    monkeypatch.setenv(_s2s.SIGNING_KEY_ENV, "fictional-signing-key")
    out = _s2s.headers(_REMOTE, _ACTOR)
    expected = hmac.new(b"fictional-signing-key", _ACTOR.encode(), hashlib.sha256).hexdigest()
    assert out[_ACTOR_HEADER] == _ACTOR
    assert out[_ACTOR_SIG_HEADER] == expected


# --------------------------------------------------------------------------- #
# Where the refusal happens, and why it is this module's job at all
# --------------------------------------------------------------------------- #
def test_an_unusable_credential_refuses_before_the_socket_is_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refusal after the request left would have already leaked the unauthenticated call."""

    def _never_called(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a request left the process with an unusable credential")

    monkeypatch.setattr(_s2s.urllib.request, "urlopen", _never_called)
    with pytest.raises(ValueError, match=_s2s.TOKEN_ENV):
        _s2s.post_json(_REMOTE, "/v1/screen", {"text": "hello", "direction": "inbound"})


def test_the_commons_reader_now_tells_the_two_states_apart_by_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Why no local resolution is needed, asserted rather than asserted-about.

    `client_headers` tells UNSET and SET-AND-EMPTY apart at the source, so a local resolution
    here would restate that rule rather than check it, and a rule restated in two places drifts
    in two places.

    This case is the one place that pins the guarantee `adapters/gcp/_s2s.py` DELEGATES instead
    of restating, so a commons that regressed to two states would fail here and name the module
    that has to grow the rule back.
    """
    monkeypatch.delenv(_s2s.TOKEN_ENV, raising=False)
    unset = client_headers(_ACTOR, token_env=_s2s.TOKEN_ENV, signing_key_env=_s2s.SIGNING_KEY_ENV)
    assert unset == {}, (
        "UNSET must stay the offline zero-secret posture: no header, nothing raised. The whole "
        "gate runs in that state."
    )

    monkeypatch.setenv(_s2s.TOKEN_ENV, "")
    with pytest.raises(ConfiguredEmptyError, match=_s2s.TOKEN_ENV) as raised:
        client_headers(_ACTOR, token_env=_s2s.TOKEN_ENV, signing_key_env=_s2s.SIGNING_KEY_ENV)
    assert "never inherits the unset default" in str(raised.value), (
        "hex_service_kit.s2s.client_headers has stopped distinguishing unset from set-and-empty. "
        "adapters/gcp/_s2s.py delegates that rule to it and no longer resolves the names itself, "
        "so an emptied credential is once again sending no Authorization header. Restore the "
        "local resolution, or pin a commons that keeps the guarantee."
    )


def test_the_bearer_requirement_is_this_modules_own_rule_not_the_commons_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The commons omits an absent bearer by default; `require_token=` is what refuses it.

    The asymmetry is the reason `headers()` still passes an argument at all, so it is worth one
    case: delete the argument and this fails while every other case here stays green.
    """
    monkeypatch.delenv(_s2s.TOKEN_ENV, raising=False)
    assert client_headers(token_env=_s2s.TOKEN_ENV, signing_key_env=_s2s.SIGNING_KEY_ENV) == {}
    with pytest.raises(ValueError, match=_s2s.TOKEN_ENV):
        client_headers(
            token_env=_s2s.TOKEN_ENV,
            signing_key_env=_s2s.SIGNING_KEY_ENV,
            require_token=True,
        )
