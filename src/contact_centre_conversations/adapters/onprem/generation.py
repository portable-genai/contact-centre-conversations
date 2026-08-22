"""On-prem GenerationPort: fail-fast portability placeholder (P-12).

The client runs its own model, inside its own boundary. Refusing is right: a placeholder that
returned None would be read as "the model declined", which is a normal outcome, and the copilot
would quietly ship with no suggestions and a green gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...config import Settings
from ...domain.models import RetrievedPassage


class OnPremGenerationAdapter:
    """Satisfies GenerationPort but refuses: bind the client's own model endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def draft(
        self, prompt: str, passages: Sequence[RetrievedPassage]
    ) -> Mapping[str, object] | None:
        raise NotImplementedError(
            "on-prem generation is a portability placeholder: bind the client's own model "
            "endpoint (see docs/onprem-migration.md)."
        )
