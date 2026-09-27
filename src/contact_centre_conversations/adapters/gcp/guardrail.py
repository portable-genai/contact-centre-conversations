"""Platform-remote GuardrailPort: the agent-guardrail-gateway Agent Guardrail Gateway client.

    POST <base>/v1/guardrail/screen  {"text": str, "direction": "input"}
    -> {"allowed": bool, "direction": str, "findings": [{"category", "confidence", "detail"}],
        "sanitized_text": str | None, "reason": str}

This is the gateway's real, documented contract (``agent-guardrail-gateway/SPEC.md`` section 6),
the same one ``compliance_advisory.adapters.platform.remote_guardrail`` consumes. Three rules this
client keeps:

* it screens the ALREADY-REDACTED text, so the raw identifiers never leave the process to solve
  a problem that has nothing to do with them;
* it authenticates the way the gateway's ``gcp`` profile verifies: a Google-signed OIDC ID token
  minted per call for ``guardrail_audience`` (``GUARDRAIL_GATEWAY_AUDIENCE``, which must equal
  the gateway's ``GUARDRAIL_S2S_AUDIENCE``) from this service's workload identity. The static
  bearer override is the guardrail's OWN ``GUARDRAIL_GATEWAY_S2S_TOKEN``, never the shared
  ``S2S_TOKEN``: the commons sends a static bearer in preference to minting, so reading the
  shared name would send the knowledge base's credential to a gateway that rejects it;
* it RAISES on any failure, and on any response it cannot read as a verdict. ``allowed`` must
  be a JSON boolean: a string ``"false"`` is truthy, so reading it loosely would turn a refusal
  into a pass. ``TurnGuard`` turns the raise into ``UNAVAILABLE``, which fails closed per mode.
  Returning CLEAN on a transport error would be the single most dangerous line in this
  repository.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ScreenOutcome, ScreenResult
from ._s2s import post_json, require_base_url

#: The gateway's ``Direction`` enum value for a customer turn screened before generation. This
#: port only ever screens inbound turns (see ``ports/guardrail.py``), so the value is fixed here
#: rather than threaded through as a parameter.
_DIRECTION = "input"

#: The guardrail's own static-bearer override. Unset in a ``gcp`` deployment, where the bearer is
#: the minted ID token; kept apart from the shared ``S2S_TOKEN`` because a static bearer always
#: wins over minting, and the shared one belongs to siblings that verify a different credential.
TOKEN_ENV = "GUARDRAIL_GATEWAY_S2S_TOKEN"


class PlatformGuardrailAdapter:
    """Screen one turn through the shared agent-guardrail-gateway."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        base = require_base_url(
            self._settings.guardrail_url, what="guardrail_url (agent-guardrail-gateway)"
        )
        payload = post_json(
            base,
            "/v1/guardrail/screen",
            {"text": text, "direction": _DIRECTION},
            token_env=TOKEN_ENV,
            audience=self._settings.guardrail_audience.strip(),
        )
        allowed = payload.get("allowed")
        if not isinstance(allowed, bool):
            # An unrecognised response is not a pass. A missing field, a null, or a string
            # ``"false"`` (which is truthy) is a gateway this client does not understand, and
            # the only safe reading of that is "did not screen".
            raise ValueError(
                f"agent-guardrail-gateway response carried no boolean 'allowed' field: {payload!r}"
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
            outcome=ScreenOutcome.CLEAN if allowed else ScreenOutcome.BLOCKED,
            turn_index=turn_index,
            detail=detail,
            categories=categories,
        )
