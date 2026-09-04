"""Platform-remote RetrievalPort: the enterprise-knowledge-base governed-RAG client.

**This adapter is the PROPOSED shape recorded in ``ports/retrieval.py``.** At the time E1 was built,
no repository in the catalog shipped a remote enterprise-knowledge-base retrieval adapter, so this
is the first, and it is written down rather than left to the next consumer to invent again:

    POST <base>/v1/retrieve
    {"query": str, "top_k": int, "filters": {str: str}}
    -> {"passages": [{"text": str, "score": float,
                      "citation": {"source_id": str, "title": str, "snippet": str}}]}

Governance lives on the enterprise-knowledge-base side (the index is partitioned and
access-controlled there); this client passes the market and locale as FILTERS so the partition is
enforced by the service that owns it rather than requested politely in prompt text.

No cloud SDK: enterprise-knowledge-base is a sibling service in this catalog, reached over plain
HTTP with the shared S2S headers, so this module imports with nothing installed.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.kernel import Citation
from ...domain.models import RetrievalQuery, RetrievedPassage
from ._s2s import post_json, require_base_url


class PlatformRetrievalAdapter:
    """Retrieve cited passages from the shared enterprise-knowledge-base knowledge base."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        base = require_base_url(
            self._settings.retrieval_url, what="retrieval_url (enterprise-knowledge-base)"
        )
        payload = post_json(
            base,
            "/v1/retrieve",
            {"query": query.text, "top_k": query.top_k, "filters": dict(query.filters)},
        )
        rows = payload.get("passages")
        if not isinstance(rows, list):
            raise ValueError("enterprise-knowledge-base returned no 'passages' array")
        passages: list[RetrievedPassage] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            citation = row.get("citation")
            if not isinstance(citation, dict) or not citation.get("source_id"):
                # A passage with no provenance is not admissible, so it is dropped rather than
                # carried with an invented citation.
                continue
            passages.append(
                RetrievedPassage(
                    text=str(row.get("text", "")),
                    citation=Citation(
                        source_id=str(citation["source_id"]),
                        title=str(citation.get("title", "")),
                        snippet=str(citation.get("snippet", "")),
                    ),
                    score=float(row.get("score", 0.0)),
                )
            )
        return passages
