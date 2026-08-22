"""The two-mode gate: agent-assist and self-service, separately enabled, both born OFF.

E1 is one repository, one shared kernel (transcripts, knowledge base, audit) and TWO modes with
different risk postures:

* **agent-assist** is internal decision-support. A human agent is on the contact, reads the
  whisper panel and decides. The worst failure is a bad suggestion a trained agent ignores.
* **self-service** is customer-facing. Nobody stands between the engine and the customer, so
  the same defect reaches a member of the public directly.

They therefore promote separately: each is its own Hrz4 gated release with its own rubric set
and its own promotion evidence, which is the entire reason the catalog row separates them. A
single "the service is live" switch would let the safer mode's evidence promote the riskier one.

**Both flags default OFF and resolve in three states**, like every other setting in this repo
(see ``config.py``). The settings file writes the UNSET default explicitly, so:

* UNSET   -> the file's written ``off``. A deployment that configured nothing serves no mode.
* EMPTY   -> :class:`ModeConfigurationError` at boot. An operator who emptied the value
  expressed an intent and it names no posture, so it must not inherit the unset default.
* UNKNOWN -> :class:`ModeConfigurationError` at boot, including a mis-capitalised ``On``.
* VALID   -> honoured exactly.

And a mode that is ON without its own promotion evidence refuses to boot under any profile
other than a deliberate offline ``local``: shipping a customer-facing mode to a managed
deployment on the strength of somebody else's rubric run is the failure this check removes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hex_service_kit.enums import LenientStrEnum

#: Tokens that mean ON, exactly. Compared case-sensitively for the same reason the profile is:
#: normalising a typo turns it into a silent choice, and refusing it turns it into a boot error.
_ON: frozenset[str] = frozenset({"on", "true", "1", "yes", "enabled"})
#: Tokens that mean OFF, exactly.
_OFF: frozenset[str] = frozenset({"off", "false", "0", "no", "disabled"})

#: The profile under which a mode may run without registered promotion evidence: the deliberate
#: offline one, where there is no customer and no live console to promote to.
_EVIDENCE_EXEMPT_PROFILE = "local"


class ContactMode(LenientStrEnum):
    """Which of the two separately gated modes a request belongs to."""

    AGENT_ASSIST = "agent_assist"
    SELF_SERVICE = "self_service"


class ModeConfigurationError(ValueError):
    """The mode block is unreadable, contradictory, or claims a promotion nobody evidenced."""


class ModeDisabledError(RuntimeError):
    """This mode is not enabled in this deployment, so the route refuses.

    Carries its own HTTP status so the API answers 503 (the service is not configured to do
    this) rather than 403 (you are not allowed to), which is the difference between an operator
    reading the settings file and a user reading their entitlements.
    """

    http_status: int = 503


@dataclass(frozen=True, slots=True)
class ModeGate:
    """One mode's release state: is it on, and what promotion evidence does it stand on."""

    mode: ContactMode
    enabled: bool = False
    #: The Hrz4 metric bundle whose promotion verdict authorises THIS mode. Empty means none.
    promotion_bundle: str = ""

    def require(self) -> None:
        """Raise :class:`ModeDisabledError` unless this mode is enabled."""
        if not self.enabled:
            raise ModeDisabledError(
                f"mode {self.mode.value!r} is not enabled in this deployment: set it on in the "
                "modes block of config/settings.yaml, with its own Hrz4 promotion bundle"
            )


def _flag(raw: Any, *, field: str) -> bool:
    """Read one three-state flag. Empty and unknown both refuse; nothing inherits a default."""
    if raw is None:
        raise ModeConfigurationError(
            f"modes.{field} is missing: every mode names its state explicitly, because an "
            "absent flag is not a request to serve"
        )
    text = str(raw)
    if not text.strip():
        raise ModeConfigurationError(
            f"modes.{field} is set to an empty value, which is not a state. A value that was "
            "deliberately emptied does not inherit the shipped default; write on or off."
        )
    if text in _ON:
        return True
    if text in _OFF:
        return False
    raise ModeConfigurationError(
        f"modes.{field}={text!r} is not a state (exact case). "
        f"Use one of {sorted(_ON)} or {sorted(_OFF)}."
    )


@dataclass(frozen=True, slots=True)
class ModeGates:
    """Both gates, resolved once. Absence of a mode block is both modes OFF, never both ON."""

    agent_assist: ModeGate = ModeGate(mode=ContactMode.AGENT_ASSIST)
    self_service: ModeGate = ModeGate(mode=ContactMode.SELF_SERVICE)

    @classmethod
    def all_off(cls) -> ModeGates:
        """The born state: nothing serves until a deployment says so."""
        return cls()

    @classmethod
    def both_on(cls, *, bundle: str = "contact-centre-conversations") -> ModeGates:
        """Both gates on, each with its own promotion bundle.

        Used by the test suite and the demo, which have to reach both modes. It is a named
        constructor rather than a default so that turning a mode on is always something written
        down somewhere: there is no code path where both modes come on because nobody spoke.
        """
        return cls(
            agent_assist=ModeGate(
                mode=ContactMode.AGENT_ASSIST,
                enabled=True,
                promotion_bundle=f"{bundle}-agent-assist",
            ),
            self_service=ModeGate(
                mode=ContactMode.SELF_SERVICE,
                enabled=True,
                promotion_bundle=f"{bundle}-self-service",
            ),
        )

    def gate(self, mode: ContactMode) -> ModeGate:
        return self.agent_assist if mode is ContactMode.AGENT_ASSIST else self.self_service

    def require(self, mode: ContactMode) -> ModeGate:
        """Return the gate for ``mode``, or refuse when it is not enabled."""
        gate = self.gate(mode)
        gate.require()
        return gate

    @property
    def any_enabled(self) -> bool:
        return self.agent_assist.enabled or self.self_service.enabled

    @property
    def enabled_modes(self) -> tuple[ContactMode, ...]:
        return tuple(g.mode for g in (self.agent_assist, self.self_service) if g.enabled)

    @classmethod
    def resolve(
        cls,
        block: Mapping[str, Any] | None,
        *,
        profile: str,
        profile_explicit: bool,
    ) -> ModeGates:
        """Resolve the settings file's ``modes:`` block, refusing at BOOT on anything unclear.

        ``block`` is None when the settings file carries no modes block at all, which is the
        one case that is not an error: a repo with no mode configuration serves no mode. Any
        block that IS present must name both modes completely.
        """
        if block is None:
            return cls.all_off()
        if not isinstance(block, Mapping):
            raise ModeConfigurationError("settings 'modes' must be a mapping of mode -> flags")
        unknown = sorted(set(block) - {m.value for m in ContactMode})
        if unknown:
            raise ModeConfigurationError(
                f"settings 'modes' names unknown modes {unknown}; "
                f"the modes are {[m.value for m in ContactMode]}"
            )
        gates = {
            mode: cls._one(mode, block.get(mode.value), profile=profile) for mode in ContactMode
        }
        resolved = cls(
            agent_assist=gates[ContactMode.AGENT_ASSIST],
            self_service=gates[ContactMode.SELF_SERVICE],
        )
        if resolved.any_enabled and not profile_explicit:
            raise ModeConfigurationError(
                "a mode is enabled but no profile was chosen: an unconfigured deployment must "
                "not serve either mode. Name a profile in CONTACT_PROFILE."
            )
        return resolved

    @staticmethod
    def _one(mode: ContactMode, entry: Any, *, profile: str) -> ModeGate:
        if entry is None:
            raise ModeConfigurationError(
                f"settings 'modes' is present but does not name {mode.value!r}: a partially "
                "written mode block is how one mode ends up on a state nobody chose"
            )
        if not isinstance(entry, Mapping):
            raise ModeConfigurationError(f"settings 'modes.{mode.value}' must be a mapping")
        enabled = _flag(entry.get("enabled"), field=f"{mode.value}.enabled")
        bundle = str(entry.get("promotion_bundle") or "").strip()
        if enabled and not bundle and profile != _EVIDENCE_EXEMPT_PROFILE:
            raise ModeConfigurationError(
                f"mode {mode.value!r} is enabled under the {profile!r} profile with no "
                "promotion_bundle: each mode promotes on ITS OWN Hrz4 evidence, so name the "
                "bundle whose rubric set authorised this mode"
            )
        return ModeGate(mode=mode, enabled=enabled, promotion_bundle=bundle)
