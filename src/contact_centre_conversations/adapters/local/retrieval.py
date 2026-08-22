"""Local RetrievalPort: an offline fixture knowledge base that actually answers.

Reads a JSON Lines corpus from ``kb_path`` (``config/kb/passages.jsonl`` by default) and ranks
by normalised term overlap, using the same locale-aware normalisation the phrase matcher uses so
that a Japanese query is folded the way a Japanese lexicon is. Deterministic: same query, same
corpus, same order, every run, which is what makes a citation-accuracy metric replayable.

It is a stand-in for Hrz2, not a search product, and it is deliberately honest about the two
ways it can fail:

* a corpus file that was NAMED and does not exist raises, rather than answering from nothing;
* an EMPTY corpus raises, rather than returning ``[]``. Empty retrieval means "say nothing" in
  ``domain/suggestions.py``, so an unreachable knowledge base reported as an empty result would
  silently turn a broken deployment into a quiet one.
"""

from __future__ import annotations

import json
from pathlib import Path

from speech_lexicon_kit import normalise

from ...config import Settings
from ...domain.kernel import Citation
from ...domain.models import RetrievalQuery, RetrievedPassage

#: Locale used to normalise the corpus and the query when the query names none.
_FALLBACK_LOCALE = "en"


class LocalFixtureRetrievalAdapter:
    """Rank a fictional fixture corpus by term overlap, deterministically."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = Path(settings.kb_path) if settings.kb_path else None

    def _corpus(self) -> list[dict[str, str]]:
        if self._path is None:
            raise RuntimeError(
                "no knowledge-base corpus is configured (kb_path is empty), so this deployment "
                "can ground nothing. Point kb_path at a passage file or bind a real Hrz2 adapter."
            )
        if not self._path.exists():
            raise RuntimeError(f"knowledge-base corpus {self._path} does not exist")
        rows: list[dict[str, str]] = []
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            rows.append({str(k): str(v) for k, v in row.items()})
        if not rows:
            raise RuntimeError(
                f"knowledge-base corpus {self._path} is empty: an empty corpus is a broken "
                "deployment, and returning no passages would look like a well-grounded silence"
            )
        return rows

    @staticmethod
    def _terms(text: str, locale: str) -> set[str]:
        folded = normalise(text, locale).text
        return {token for token in folded.split() if len(token) > 2}

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        locale = query.filters.get("locale") or _FALLBACK_LOCALE
        wanted = self._terms(query.text, locale)
        scored: list[tuple[float, str, RetrievedPassage]] = []
        for row in self._corpus():
            if not self._matches_filters(row, query):
                continue
            overlap = wanted & self._terms(row.get("text", ""), locale)
            if not overlap:
                continue
            score = round(len(overlap) / max(len(wanted), 1), 4)
            passage = RetrievedPassage(
                text=row["text"],
                citation=Citation(
                    source_id=row["passage_id"],
                    title=row.get("title", row["passage_id"]),
                    snippet=row["text"][:120],
                ),
                score=score,
            )
            scored.append((score, row["passage_id"], passage))
        # Sorted by score descending then id ascending: a stable order is what makes the
        # citation-accuracy metric comparable between runs.
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [passage for _, _, passage in scored[: max(query.top_k, 1)]]

    @staticmethod
    def _matches_filters(row: dict[str, str], query: RetrievalQuery) -> bool:
        """A filter the corpus does not carry excludes nothing; one it carries must match.

        The partition is enforced by the adapter rather than asked for in prompt text, which is
        the property a governed knowledge base has to have.
        """
        for key, value in query.filters.items():
            present = row.get(key)
            if present is not None and present != value:
                return False
        return True
