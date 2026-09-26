"""Platform-remote GuardrailPort: the agent-guardrail-gateway Agent Guardrail Gateway client.

    POST <base>/v1/guardrail/screen  {"text": str, "direction": "input"}
    -> {"allowed": bool, "direction": str, "findings": [{"category", "confidence", "detail"}],
        "sanitized_text": str | None, "reason": str}

This is the gateway's real, documented contract (``agent-guardrail-gateway/SPEC.md`` section 6),
the same one ``compliance_advisory.adapters.platform.remote_guardrail`` consumes. Two rules this
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

#: The gateway's ``Direction`` enum value for a customer turn screened before generation. This
#: port only ever screens inbound turns (see ``ports/guardrail.py``), so the value is fixed here
#: rather than threaded through as a parameter.
_DIRECTION = "input"


class PlatformGuardrailAdapter:
    """Screen one turn through the shared agent-guardrail-gateway."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        base = require_base_url(
            self._settings.guardrail_url, what="guardrail_url (agent-guardrail-gateway)"
        )
        payload = post_json(base, "/v1/guardrail/screen", {"text": text, "direction": _DIRECTION})
        if "allowed" not in payload:
            # An unrecognised response is not a pass. It is a gateway this client does not
            # understand, and the only safe reading of that is "did not screen".
            raise ValueError(
                f"agent-guardrail-gateway response carried no 'allowed' field: {payload!r}"
            )
        findings = payload.get("findings")
        categories = (
            tuple(
                str(item["category"])
                for item in findings
                if isinstance(item, dict) and "category" in item
            )
            if isinstance(findings, list)
            else ()
        )
        detail = str(payload.get("reason", ""))
        if not detail and findings and isinstance(findings, list):
            first = findings[0]
            if isinstance(first, dict):
                detail = str(first.get("detail", ""))
        return ScreenResult(
            outcome=ScreenOutcome.CLEAN if payload["allowed"] else ScreenOutcome.BLOCKED,
            turn_index=turn_index,
            detail=detail,
            categories=categories,
        )
