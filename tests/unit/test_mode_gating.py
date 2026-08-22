"""Two-mode gating: both flags default OFF, resolve three-state, and refuse at BOOT.

The catalog row separates E1's two modes because they carry different risk. This suite is the
gate that keeps the separation real, and every case here is a way the separation could quietly
stop existing:

* both flags off and every mode route refuses (the born state);
* one mode on grants nothing to the other;
* an EMPTIED flag refuses to boot rather than inheriting the shipped ``off``;
* an unknown or mis-capitalised flag refuses to boot;
* a mode enabled with no promotion evidence refuses to boot under a non-local profile.
"""

from __future__ import annotations

import pytest

from contact_centre_conversations import services
from contact_centre_conversations.config import (
    Settings,
    build_container,
)
from contact_centre_conversations.domain.kernel import (
    utcnow,
)
from contact_centre_conversations.domain.modes import (
    ContactMode,
    ModeConfigurationError,
    ModeDisabledError,
    ModeGate,
    ModeGates,
)

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_ON = {"enabled": "on", "promotion_bundle": "bundle-x"}
_OFF = {"enabled": "off", "promotion_bundle": ""}


def _resolve(block: object, *, profile: str = "local", explicit: bool = True) -> ModeGates:
    return ModeGates.resolve(block, profile=profile, profile_explicit=explicit)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# The born state
# --------------------------------------------------------------------------- #
def test_a_settings_file_with_no_modes_block_serves_neither_mode() -> None:
    gates = _resolve(None)
    assert gates.enabled_modes == ()
    assert gates.any_enabled is False


def test_a_directly_constructed_settings_object_serves_neither_mode() -> None:
    """Direct construction is what an embedder and a unit test do; it must inherit the OFF state."""
    assert Settings(profile="local").modes.enabled_modes == ()


@pytest.mark.parametrize("mode", list(ContactMode))
def test_with_both_flags_off_every_mode_route_refuses(mode: ContactMode) -> None:
    container = build_container(local_settings(modes=ModeGates.all_off()))
    with pytest.raises(ModeDisabledError) as excinfo:
        services.require_mode(container, mode)
    assert excinfo.value.http_status == 503


def test_enabling_one_mode_grants_nothing_to_the_other() -> None:
    only_assist = ModeGates(
        agent_assist=ModeGate(mode=ContactMode.AGENT_ASSIST, enabled=True, promotion_bundle="b"),
        self_service=ModeGate(mode=ContactMode.SELF_SERVICE),
    )
    container = build_container(local_settings(modes=only_assist))
    assert services.require_mode(container, ContactMode.AGENT_ASSIST).enabled is True
    with pytest.raises(ModeDisabledError):
        services.require_mode(container, ContactMode.SELF_SERVICE)


# --------------------------------------------------------------------------- #
# Three states of one flag
# --------------------------------------------------------------------------- #
def test_an_emptied_flag_refuses_rather_than_inheriting_the_shipped_off() -> None:
    """An operator who emptied a value expressed an intent, and it names no state."""
    with pytest.raises(ModeConfigurationError, match="empty"):
        _resolve({"agent_assist": {"enabled": "  "}, "self_service": _OFF})


@pytest.mark.parametrize("value", ["On", "OFF", "yes please", "maybe", "1.0"])
def test_an_unknown_or_miscapitalised_flag_refuses(value: str) -> None:
    with pytest.raises(ModeConfigurationError):
        _resolve({"agent_assist": {"enabled": value}, "self_service": _OFF})


def test_a_half_written_mode_block_refuses() -> None:
    with pytest.raises(ModeConfigurationError, match="self_service"):
        _resolve({"agent_assist": _ON})


def test_an_unknown_mode_name_refuses() -> None:
    with pytest.raises(ModeConfigurationError, match="unknown"):
        _resolve({"agent_assist": _ON, "self_service": _OFF, "shadow_mode": _ON})


@pytest.mark.parametrize("value", ["on", "true", "1", "yes", "enabled"])
def test_every_documented_on_token_is_honoured(value: str) -> None:
    gates = _resolve(
        {"agent_assist": {"enabled": value, "promotion_bundle": "b"}, "self_service": _OFF}
    )
    assert gates.agent_assist.enabled is True


# --------------------------------------------------------------------------- #
# Promotion evidence
# --------------------------------------------------------------------------- #
def test_a_mode_enabled_without_promotion_evidence_refuses_off_local() -> None:
    """Each mode promotes on ITS OWN Hrz4 evidence, so an unevidenced one must not boot."""
    with pytest.raises(ModeConfigurationError, match="promotion_bundle"):
        _resolve(
            {"agent_assist": {"enabled": "on"}, "self_service": _OFF},
            profile="gcp",
        )


def test_the_same_configuration_is_allowed_under_a_deliberate_local() -> None:
    gates = _resolve({"agent_assist": {"enabled": "on"}, "self_service": _OFF}, profile="local")
    assert gates.agent_assist.enabled is True
    assert gates.agent_assist.promotion_bundle == ""


def test_a_mode_cannot_be_enabled_when_nobody_chose_a_profile() -> None:
    """An unconfigured deployment must not serve either mode, whatever the modes block says."""
    with pytest.raises(ModeConfigurationError, match="no profile was chosen"):
        _resolve({"agent_assist": _ON, "self_service": _OFF}, explicit=False)


# --------------------------------------------------------------------------- #
# The gate is checked BEFORE any work
# --------------------------------------------------------------------------- #
def test_a_disabled_mode_touches_no_store_and_no_model() -> None:
    """A refusal that had already written a turn would be a refusal after the fact."""
    container = build_container(local_settings(modes=ModeGates.all_off()))
    built = services.build_services(container)
    with pytest.raises(ModeDisabledError):
        services.require_mode(container, ContactMode.AGENT_ASSIST)
    stored = container.contact_store.turns(
        sample_cases.CLEAN_CONTACT_ID, tenant=sample_cases.TENANT
    )
    assert stored == ()
    # And the service itself is constructible: the gate is a route check, not a wiring failure.
    assert built.agent_assist is not None
    assert utcnow().tzinfo is not None
