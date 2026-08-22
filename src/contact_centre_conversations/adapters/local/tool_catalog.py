"""Local ToolCatalogPort: the fixture executor, and the count that proves maker-checker.

``describe`` reads the action catalog packs, so the ``consequential`` metadata is the same data
the engine and the runbook read; there is no second, kinder copy of it in the adapter.

``execute`` records every call. That counter is not decoration: "a consequential action results
in ZERO adapter calls" is a claim about something that did NOT happen, and only counting calls
can prove it. The adapter also refuses a consequential action itself, as a second, independent
fail-closed point: the engine should never route one here, and if a future refactor did, the
adapter would still not perform it.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ActionCall, ActionOutcome, ActionSpec


class LocalFixtureToolCatalog:
    """Describe actions from the packs; execute only non-consequential ones, and count."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._calls: list[ActionCall] = []

    @property
    def calls(self) -> tuple[ActionCall, ...]:
        """Every execution this adapter was asked to perform, for tests and the demo panel."""
        return tuple(self._calls)

    def describe(self, action_id: str) -> ActionSpec | None:
        return self._settings.packs.action_spec(action_id)

    def execute(self, call: ActionCall) -> ActionOutcome:
        spec = self.describe(call.action_id)
        if spec is None:
            raise LookupError(f"no catalog declares action {call.action_id!r}")
        if spec.consequential:
            # The engine must never get here. If it does, this is the second wall.
            raise PermissionError(
                f"action {call.action_id!r} is consequential and may not be executed by this "
                "service; it goes to maker-checker (rule R8) and a human executes it"
            )
        self._calls.append(call)
        reference = f"fixture:{call.action_id}:{len(self._calls)}"
        return ActionOutcome(
            action_id=call.action_id,
            executed=True,
            detail=f"executed {spec.title!r} against the offline fixture",
            reference=reference,
        )
