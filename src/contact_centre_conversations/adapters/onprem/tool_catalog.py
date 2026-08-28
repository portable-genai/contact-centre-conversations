"""On-prem ToolCatalogPort: fail-fast portability placeholder (P-12).

The client's action catalog and its executors are theirs. ``describe`` refuses as loudly as
``execute`` does, deliberately: returning None from ``describe`` would read as "no such action",
which the engine treats as a clean denial, and the deployment would look correctly restrictive
while actually being unwired.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ActionCall, ActionOutcome, ActionSpec

_MESSAGE = (
    "on-prem action execution is a portability placeholder: bind the client's own A2A/MCP "
    "catalog and executors (see docs/onprem-migration.md). Consequential actions still never "
    "auto-execute."
)


class OnPremToolCatalog:
    """Satisfies ToolCatalogPort but refuses on both methods."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def describe(self, action_id: str, vertical: str) -> ActionSpec | None:
        raise NotImplementedError(_MESSAGE)

    def execute(self, call: ActionCall) -> ActionOutcome:
        raise NotImplementedError(_MESSAGE)
