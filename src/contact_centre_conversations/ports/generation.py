"""GenerationPort: the one seam a language model reaches, and the narrowest one in the repo.

The model drafts a reply from passages that were already retrieved and already screened. It
receives a redacted turn and a passage list, and it returns a JSON object that
``domain/suggestions.py`` validates and discards on any failure.

What this port deliberately does NOT offer: free-form completion, a tool-calling loop, a
conversation history parameter, or anything that would let a caller ask the model a question the
knowledge base did not already answer. A wider port is a wider blast radius, and every widening
of it would have to be argued against the determinism rule.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from ..domain.models import RetrievedPassage


@runtime_checkable
class GenerationPort(Protocol):
    def draft(
        self, prompt: str, passages: Sequence[RetrievedPassage]
    ) -> Mapping[str, object] | None:
        """Draft a grounded reply as a JSON object, or None when the model declines.

        The return value is UNTRUSTED. Callers pass it straight to
        ``suggestions.validate_draft``, which is the only thing permitted to believe it.
        """
        ...
