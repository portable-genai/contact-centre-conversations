"""KB-grounded suggested replies: the only place a model's words reach a person, and the checks.

The model's job in E1 is exactly one sentence long: turn passages that a governed knowledge base
already returned into a reply an agent can read out. It does not decide the next step, it does
not decide whether an action may run, it does not decide whether a disclosure was made, and it
does not produce a number.

Four rules, all enforced here rather than asked for in a prompt:

* **Empty retrieval means no suggestion.** Not a general-purpose answer, not a hedge, not "I
  could not find anything but here is what I think". Nothing. The whole value of a grounded
  copilot is that its silence is informative.
* **Schema or discard.** The model returns a JSON object. Anything that is not one, or that
  omits a field, or whose fields have the wrong type, is discarded whole. There is no partial
  acceptance and no repair pass: repairing malformed output is how an unvalidated field gets
  through in the shape the repairer expected.
* **Every cited passage must be one that was actually retrieved.** A model naming a passage id
  it was not given is fabricating provenance, which is worse than fabricating text because it
  looks checked.
* **No number the passages do not contain.** The house rule is that the model never produces a
  figure. Enforcing it as "every digit run in the draft appears in a cited passage" is crude and
  it is checkable, which beats a prompt that asks nicely.

A discarded draft is not an error. It is the system declining to say something, and the caller
shows the deterministic panel without a suggestion.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .models import (
    AUDIENCE_INTERNAL,
    AUDIENCE_PUBLIC,
    RetrievalQuery,
    RetrievedPassage,
    SuggestedReply,
)
from .modes import ContactMode

__all__ = [
    "AUDIENCES_FOR_MODE",
    "MAX_SUGGESTION_CHARS",
    "build_query",
    "passage_id",
    "validate_draft",
]

#: Which audiences each mode may ground a reply in. Agent-assist is read by a trained employee
#: who is meant to see handling rules; self-service is read by the customer, so it may quote
#: only what the bank has published. The asymmetry is the whole reason the modes are gated
#: apart, and it is data here rather than a branch so a third mode has to state its position.
AUDIENCES_FOR_MODE: dict[ContactMode, frozenset[str]] = {
    ContactMode.AGENT_ASSIST: frozenset({AUDIENCE_PUBLIC, AUDIENCE_INTERNAL}),
    ContactMode.SELF_SERVICE: frozenset({AUDIENCE_PUBLIC}),
}

#: A whisper panel is read at a glance while somebody is talking. Longer than this is not a
#: suggestion, it is a document, and an agent will read it aloud badly.
MAX_SUGGESTION_CHARS = 320

_DIGITS = re.compile(r"\d[\d,.]*")


def passage_id(passage: RetrievedPassage) -> str:
    """The stable id a draft cites a passage by: its citation's source id."""
    return passage.citation.source_id


def build_query(
    text: str,
    *,
    market: str,
    locale: str,
    vertical: str,
    mode: ContactMode,
    top_k: int = 5,
) -> RetrievalQuery:
    """The governed-RAG query for one redacted customer turn.

    Market, locale, vertical and audience are FILTERS rather than prompt text, so a knowledge
    base that partitions by jurisdiction, by line of business, or by who a passage was written
    for can enforce the partition itself instead of trusting a phrasing. The vertical matters
    for the same reason the packs are keyed by it: an insurer's policy wording and a bank's are
    different reviewed corpora, and a term they happen to share is not a reason to cross them.

    The audience filter is set only where it NARROWS: a customer-facing turn asks for public
    passages, and an agent-assist turn asks for no audience at all because it may see both. A
    filter naming every permitted value would be a filter that excludes nothing while looking
    like a control.
    """
    filters = {"market": market, "locale": locale, "vertical": vertical}
    permitted = AUDIENCES_FOR_MODE[mode]
    if len(permitted) == 1:
        filters["audience"] = next(iter(permitted))
    return RetrievalQuery(text=text.strip(), top_k=top_k, filters=filters)


def _numbers(text: str) -> set[str]:
    """Digit runs, normalised so that "1,000" and "1000" compare equal."""
    return {match.group(0).replace(",", "").rstrip(".") for match in _DIGITS.finditer(text)}


def validate_draft(
    payload: object,
    passages: Sequence[RetrievedPassage],
    *,
    mode: ContactMode,
) -> SuggestedReply | None:
    """Turn raw model output into a suggestion, or into nothing at all.

    Returns None on ANY failure, deliberately, and the caller does not get to know which one:
    a caller that branched on the failure reason would grow a path that used the draft anyway.
    The reason is worth logging and is not worth acting on.
    """
    if not passages:
        return None
    if not isinstance(payload, Mapping):
        return None

    text = payload.get("text")
    cited = payload.get("passage_ids")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(cited, Sequence) or isinstance(cited, str) or not cited:
        return None
    if not all(isinstance(item, str) for item in cited):
        return None
    if len(text) > MAX_SUGGESTION_CHARS:
        return None

    available = {passage_id(passage): passage for passage in passages}
    chosen = [available[str(item)] for item in cited if str(item) in available]
    if len(chosen) != len(cited):
        # At least one cited id was never retrieved: fabricated provenance, discard everything.
        return None

    # Audience, checked here as well as filtered at the adapter. The filter is the control and
    # this is the proof: a retrieval implementation that ignored the filter, or a corpus row
    # reclassified after it was indexed, would otherwise reach a customer with staff-only
    # wording and nothing downstream would notice. Discarding whole, like every other failure.
    permitted = AUDIENCES_FOR_MODE[mode]
    if any(passage.audience not in permitted for passage in chosen):
        return None

    grounded_numbers = set()
    for passage in chosen:
        grounded_numbers |= _numbers(passage.text)
    if not _numbers(text) <= grounded_numbers:
        return None

    return SuggestedReply(
        text=text.strip(),
        citations=tuple(passage.citation for passage in chosen),
        passage_ids=tuple(passage_id(passage) for passage in chosen),
        mode=mode,
    )
