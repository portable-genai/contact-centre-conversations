"""On-prem RetrievalPort: fail-fast portability placeholder (the sovereign-exit proof, P-12).

The client grounds against its own knowledge base, behind its own access controls. Refusing is
the correct failure here and the refusal is louder than usual on purpose: a retrieval adapter
that returned an EMPTY list would be read by ``domain/suggestions.py`` as "no passage, no
suggestion", which is a legitimate quiet outcome, so the exit placeholder would look like a
working deployment whose knowledge base happened to have nothing to say.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import RetrievalQuery, RetrievedPassage


class OnPremRetrievalAdapter:
    """Satisfies RetrievalPort but refuses: bind the client's own governed index."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        raise NotImplementedError(
            "on-prem retrieval is a portability placeholder: bind the client's own governed "
            "knowledge base (see docs/onprem-migration.md). Returning an empty list here would "
            "be indistinguishable from a knowledge base with nothing to say."
        )
