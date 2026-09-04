"""Platform-remote GuardrailPort: the agent-guardrail-gateway Agent Guardrail Gateway client.

    POST <base>/v1/screen  {"text": str, "direction": "inbound"}
    -> {"verdict": "clean" | "blocked", "categories": [str], "detail": str}

marketing-compliance-gate's platform family is the reference for the transport. Two rules this
client keeps:

* it screens the ALREADY-REDACTED text, so the raw identifiers never leave the process to solve
  a problem that has nothing to do with them;
* it RAISES on any failure. ``TurnGuard`` turns the raise into ``UNAVAILABLE``, which fails
  closed per mode. Returning CLEAN on a transport error would be the single most dangerous line
  in this repository.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ScreenOutcome, ScreenResult
from ._s2s import post_json, require_base_url

_DIRECTION = "inbound"


class PlatformGuardrailAdapter:
    """Screen one turn through the shared agent-guardrail-gateway."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        base = require_base_url(
            self._settings.guardrail_url, what="guardrail_url (agent-guardrail-gateway)"
        )
        payload = post_json(base, "/v1/screen", {"text": text, "direction": _DIRECTION})
        verdict = str(payload.get("verdict", "")).strip()
        if verdict not in {ScreenOutcome.CLEAN.value, ScreenOutcome.BLOCKED.value}:
            # An unrecognised verdict is not a pass. It is a gateway this client does not
            # understand, and the only safe reading of that is "did not screen".
            raise ValueError(
                f"agent-guardrail-gateway returned an unrecognised verdict {verdict!r}"
            )
        categories = payload.get("categories")
        return ScreenResult(
            outcome=ScreenOutcome(verdict),
            turn_index=turn_index,
            detail=str(payload.get("detail", "")),
            categories=tuple(str(item) for item in categories)
            if isinstance(categories, list)
            else (),
        )
