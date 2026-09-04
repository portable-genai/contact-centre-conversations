"""Local RetrievalPort: an offline fixture knowledge base that actually answers.

Reads a JSON Lines corpus from ``kb_path`` (``config/kb/passages.jsonl`` by default) and ranks
by normalised term overlap, using the same locale-aware normalisation the phrase matcher uses so
that a Japanese query is folded the way a Japanese lexicon is. Deterministic: same query, same
corpus, same order, every run, which is what makes a citation-accuracy metric replayable.

It is a stand-in for enterprise-knowledge-base, not a search product, and it is deliberately honest
about the two ways it can fail:

* a corpus file that was NAMED and does not exist raises, rather than answering from nothing;
* an EMPTY corpus raises, rather than returning ``[]``. Empty retrieval means "say nothing" in
  ``domain/suggestions.py``, so an unreachable knowledge base reported as an empty result would
  silently turn a broken deployment into a quiet one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from speech_lexicon_kit import normalise

from ...config import Settings
from ...domain.kernel import Citation
from ...domain.models import AUDIENCES, RetrievalQuery, RetrievedPassage

#: Locale used to normalise the corpus and the query when the query names none.
_FALLBACK_LOCALE = "en"

#: Runs of script written without spaces between words: CJK ideographs, kana and Hangul.
#: Text in these scripts yields one whitespace token per sentence, so it is scored by
#: character bigrams instead. Latin text never matches, so nothing else changes.
_UNSPACED = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]{2,}")


class LocalFixtureRetrievalAdapter:
    """Rank a fictional fixture corpus by term overlap, deterministically."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = Path(settings.kb_path) if settings.kb_path else None

    def _corpus(self) -> list[dict[str, str]]:
        if self._path is None:
            raise RuntimeError(
                "no knowledge-base corpus is configured (kb_path is empty), so this deployment "
                "can ground nothing. Point kb_path at a passage file or bind a real "
                "enterprise-knowledge-base adapter."
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
        for row in rows:
            self._check_classified(row)
        return rows

    def _check_classified(self, row: dict[str, str]) -> None:
        """Every passage must say who it was written for, and where to find it.

        Refused at LOAD rather than filtered optimistically at query time. The filter treats a
        key the row does not carry as excluding nothing, so an unclassified passage would match
        a public-only query and be quoted to a customer: the very outcome the classification
        exists to prevent. A corpus nobody classified must stop the deployment, not narrow it.
        """
        passage = row.get("passage_id", "(unnamed)")
        audience = row.get("audience", "")
        if audience not in AUDIENCES:
            raise RuntimeError(
                f"knowledge-base passage {passage!r} declares audience {audience!r}; it must be "
                f"one of {list(AUDIENCES)}. A passage nobody classified would match a "
                "customer-facing query, because a filter cannot exclude on a field that is absent."
            )
        if not row.get("vertical", "").strip():
            raise RuntimeError(
                f"knowledge-base passage {passage!r} names no vertical. The filter cannot exclude "
                "on a field that is absent, so an unclassified passage would answer for every "
                "line of business at once: an insurer's wording quoted at a bank's customer."
            )
        if not row.get("source_ref", "").strip():
            raise RuntimeError(
                f"knowledge-base passage {passage!r} names no source_ref. A citation that "
                "resolves only inside the bank is provenance for the bank, not for the person "
                "being told something."
            )

    @staticmethod
    def _terms(text: str, locale: str) -> set[str]:
        """Comparable units of ``text``, for scripts that space their words and scripts that do not.

        Whitespace tokens carry the Latin-script markets. They carry nothing at all in Japanese,
        which writes without spaces: a whole sentence folds to ONE token, so term overlap is
        empty unless two passages are character-identical. A stand-in that structurally cannot
        rank one of the markets this service claims to serve is not standing in for anything, it
        is hiding the market, and every JP contact would have looked like a well-grounded silence.

        So a run of unspaced script also contributes character bigrams. Crude, deterministic, and
        enough for a fixture to rank: adjacent-character overlap is the standard cheap stand-in
        for CJK segmentation, and nothing here pretends to be a tokeniser. Latin text produces no
        such runs, so the SG corpus scores exactly as it did before.
        """
        folded = normalise(text, locale).text
        terms = {token for token in folded.split() if len(token) > 2}
        for run in _UNSPACED.findall(folded):
            terms |= {run[index : index + 2] for index in range(len(run) - 1)}
        return terms

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
                    source_ref=row["source_ref"],
                ),
                score=score,
                audience=row["audience"],
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
