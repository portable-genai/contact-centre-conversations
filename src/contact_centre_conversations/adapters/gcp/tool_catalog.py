"""Managed ToolCatalogPort: the A2A / MCP action surface, over the shared S2S transport.

    POST <base>/v1/actions/describe  {"action_id": str} -> {"action": {...}} | {"action": null}
    POST <base>/v1/actions/execute   {"action_id": str, "contact_id": str, "tenant": str,
                                      "parameters": {str: str}} -> {"reference": str, ...}

The ``consequential`` flag on a described action is read from the REMOTE catalog, so a client
that reclassifies an action as consequential changes this service's behaviour without a release
here. That is the point of holding the flag as catalog metadata rather than in code.

This adapter never executes a consequential action either. The engine will not route one, and
this is the second, independent wall.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.kernel import Severity
from ...domain.models import ActionCall, ActionOutcome, ActionSpec, ParameterSpec
from ._s2s import post_json, require_base_url


class McpToolCatalog:
    """Describe and execute actions through the client's MCP / A2A action service."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return require_base_url(self._settings.tool_catalog_url, what="tool_catalog_url")

    def describe(self, action_id: str) -> ActionSpec | None:
        payload = post_json(self._base(), "/v1/actions/describe", {"action_id": action_id})
        node = payload.get("action")
        if not isinstance(node, dict):
            return None
        raw_params = node.get("parameters")
        parameters = tuple(
            ParameterSpec(
                name=str(item.get("name", "")),
                kind=str(item.get("kind", "string")),
                required=bool(item.get("required", True)),
                pattern=str(item.get("pattern", "")),
            )
            for item in (raw_params if isinstance(raw_params, list) else [])
            if isinstance(item, dict)
        )
        return ActionSpec(
            action_id=str(node.get("action_id", action_id)),
            title=str(node.get("title", action_id)),
            # ABSENT means consequential. A catalog that forgot to say is not a catalog saying no.
            consequential=bool(node.get("consequential", True)),
            parameters=parameters,
            severity=Severity(str(node.get("severity", Severity.MEDIUM.value))),
        )

    def execute(self, call: ActionCall) -> ActionOutcome:
        spec = self.describe(call.action_id)
        if spec is None:
            raise LookupError(f"the remote catalog does not declare action {call.action_id!r}")
        if spec.consequential:
            raise PermissionError(
                f"action {call.action_id!r} is consequential in the remote catalog and may not "
                "be executed by this service; it goes to maker-checker under rule R8"
            )
        payload = post_json(
            self._base(),
            "/v1/actions/execute",
            {
                "action_id": call.action_id,
                "contact_id": call.contact_id,
                "tenant": call.tenant,
                "parameters": dict(call.parameters),
            },
            actor=call.tenant,
        )
        return ActionOutcome(
            action_id=call.action_id,
            executed=True,
            detail=str(payload.get("detail", "executed")),
            reference=str(payload.get("reference", "")),
        )
