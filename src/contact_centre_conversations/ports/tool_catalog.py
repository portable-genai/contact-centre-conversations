"""ToolCatalogPort: the A2A / MCP action surface, and the executor behind it.

Two methods, and the split matters. ``describe`` answers what the catalog says about an action,
including the ``consequential`` metadata that stops it auto-executing; ``execute`` performs one.
The engine (``domain/action_engine.py``) always calls the first and only sometimes calls the
second, which is what makes "a consequential action results in ZERO executor calls" a property a
spy adapter can count rather than a claim a docstring makes.

The catalog metadata is authoritative over the caller: an action the catalog marks consequential
never runs from this service, whatever the policy gate said and however the request arrived.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import ActionCall, ActionOutcome, ActionSpec


@runtime_checkable
class ToolCatalogPort(Protocol):
    def describe(self, action_id: str, vertical: str) -> ActionSpec | None:
        """The entry ``vertical``'s catalog gives ``action_id``, or None when it declares none.

        Scoped by vertical because the catalog is: a banking catalog must not answer for an
        insurance contact just because both declare an action of that name.
        """
        ...

    def execute(self, call: ActionCall) -> ActionOutcome:
        """Execute one NON-consequential, already-validated action."""
        ...
