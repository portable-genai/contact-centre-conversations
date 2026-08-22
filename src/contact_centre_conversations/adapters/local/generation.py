"""Local GenerationPort: a deterministic offline drafter, not a language model.

It composes a reply out of the retrieved passages themselves: the leading sentence of the
highest-scoring passage, prefixed with a fixed acknowledgement. That is deliberately less
capable than a model and exactly as GROUNDED, which is the property the offline gate has to be
able to assert.

Two things it gives the suite that a model could not:

* **A replayable pipeline.** The same turn and the same corpus produce the same draft, so
  citation-accuracy and groundedness metrics measure the validator rather than the weather.
* **A source of BAD drafts on demand.** Tests construct their own payloads to exercise every
  discard path in ``domain/suggestions.py``; this adapter's job is to produce the good one.

It never emits a figure the passages do not contain, because it never emits a figure at all: it
quotes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ...config import Settings
from ...domain.models import RetrievedPassage
from ...domain.suggestions import MAX_SUGGESTION_CHARS, passage_id

_LEAD = "Based on the current policy: "


class LocalTemplateGenerationAdapter:
    """Quote the best passage back, cited. Deterministic and SDK-free."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def draft(
        self, prompt: str, passages: Sequence[RetrievedPassage]
    ) -> Mapping[str, object] | None:
        if not passages:
            return None
        best = max(passages, key=lambda passage: (passage.score, passage.citation.source_id))
        sentence = best.text.split(". ")[0].strip().rstrip(".")
        text = f"{_LEAD}{sentence}."
        if len(text) > MAX_SUGGESTION_CHARS:
            text = text[: MAX_SUGGESTION_CHARS - 1].rstrip() + "."
        return {"text": text, "passage_ids": [passage_id(best)]}
