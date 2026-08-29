"""RetrievalPort: governed retrieval over the enterprise knowledge base.

**The shape is the knowledge base's governed-RAG port shape**, copied deliberately from
``compliance-advisory``: a query in, ranked cited passages out, and nothing else. Keeping
one shape across the catalog is what lets a knowledge base be swapped for a client's own without
touching a consumer, and it is why this file declares no extra convenience method however
tempting one would be here.

**Open judgement call, recorded rather than hidden.** At the time this repo was built, no built
repo in the catalog shipped a REMOTE knowledge-base retrieval adapter in a platform family:
``marketing-compliance-gate``'s platform family covers guardrail, audit, evaluation, registry
and rules, and stops there. This repo is therefore the first to write one, and
``adapters/gcp/retrieval.py`` is a PROPOSED shape rather than an adopted one. It is frozen here
so the next consumer inherits a decision instead of making a second one: the client is an S2S
HTTP call to ``<knowledge_base_url>/v1/retrieve`` carrying ``{"query", "top_k", "filters"}`` and
expecting ``{"passages": [{"text", "score", "citation": {"source_id", "title", "snippet"}}]}``.
If ``enterprise-knowledge-base`` lands a different contract, this is the file to change and the
note to delete.

Two properties every adapter family must hold, because the domain relies on them:

* **Empty means empty.** An adapter that cannot reach its index raises; it never returns an
  empty list, because ``suggestions.validate_draft`` treats empty retrieval as "say nothing",
  and an unreachable index reported as "nothing found" would silently degrade grounding into
  silence and look like a quiet knowledge base.
* **Every passage carries a citation.** A passage with no provenance is not admissible, so it
  is not returned.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import RetrievalQuery, RetrievedPassage


@runtime_checkable
class RetrievalPort(Protocol):
    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        """Return ranked passages with citations, or raise. Never an empty list on failure."""
        ...
