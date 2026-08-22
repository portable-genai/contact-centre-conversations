"""Deterministic intent scoring: a number this code computes, never a model's confidence.

The self-service gate needs a confidence to compare against a configured floor. Taking that
number from a classifier would put the most consequential threshold in the system downstream of
a sampler: the same utterance could clear the floor on Tuesday and miss it on Wednesday, and no
replay would explain why. So the number is computed here, from phrase matches, by a formula
written down in full:

* **strength** is how much matched text the best intent accounts for, capped at 1.0 once the
  match is at least :data:`MIN_MATCH_CHARS` normalised characters. A three-character accident
  scores low; a full phrase scores 1.0.
* **distinctness** is the best intent's matched length as a share of the best plus the runner
  up. A unique match is 1.0; two intents matching equally well is 0.5.

``confidence = strength * distinctness``. That makes AMBIGUITY a first-class low score rather
than a tie broken arbitrarily, which is the behaviour the gate needs: an utterance that two
allowlisted intents fit equally is exactly the case a machine should not act on.

It is deliberately not calibrated as a probability, and it is not called one anywhere. It is a
match quality, it is reproducible, and a reviewer can recompute it by hand from the transcript
and the pack.
"""

from __future__ import annotations

from speech_lexicon_kit import Lexicon, find_matches

from .models import IntentMatch
from .packs import AllowlistPack

__all__ = ["MIN_MATCH_CHARS", "score_intents", "best_intent"]

#: Below this many matched normalised characters, a match is treated as incidental rather than
#: intentional. Six characters is short enough to admit "refund" and long enough to reject the
#: sort of two-letter coincidence that normalisation can produce across languages.
MIN_MATCH_CHARS = 6


def _matched_chars(text: str, lexicon: Lexicon) -> dict[str, tuple[int, tuple[str, ...]]]:
    """Per entry id: the number of matched characters, and the matched surface forms.

    Overlapping matches of the same entry are counted once per covered character, so a pack
    that lists a phrase and a longer phrase containing it cannot double count its way over the
    floor.
    """
    covered: dict[str, set[int]] = {}
    surfaces: dict[str, list[str]] = {}
    for match in find_matches(text, lexicon):
        covered.setdefault(match.entry_id, set()).update(range(match.char_start, match.char_end))
        surfaces.setdefault(match.entry_id, []).append(match.matched_text)
    return {
        entry_id: (len(positions), tuple(dict.fromkeys(surfaces.get(entry_id, ()))))
        for entry_id, positions in covered.items()
    }


def score_intents(pack: AllowlistPack, text: str) -> tuple[IntentMatch, ...]:
    """Score every allowlisted intent against one customer utterance, best first.

    An allowlist with no intents has no lexicon at all (see ``packs.py``), so it scores nothing
    and every caller sees an empty result. That is the fail-closed state expressed as data
    rather than as a special case somebody could forget to write.
    """
    if pack.lexicon is None or not pack.intents:
        return ()
    per_entry = _matched_chars(text, pack.lexicon)
    if not per_entry:
        return ()
    ranked = sorted(per_entry.items(), key=lambda item: (-item[1][0], item[0]))
    best_chars = ranked[0][1][0]
    runner_up_chars = ranked[1][1][0] if len(ranked) > 1 else 0
    denominator = best_chars + runner_up_chars
    distinctness = (best_chars / denominator) if denominator else 0.0

    matches: list[IntentMatch] = []
    for entry_id, (chars, surfaces) in ranked:
        strength = min(1.0, chars / MIN_MATCH_CHARS)
        # Only the best match earns the distinctness it computed; every other candidate is by
        # definition contested, and reporting it as confident would hide the ambiguity.
        share = distinctness if entry_id == ranked[0][0] else 0.0
        matches.append(
            IntentMatch(
                intent_id=entry_id,
                confidence=round(strength * share, 4),
                matched_phrases=surfaces,
            )
        )
    return tuple(matches)


def best_intent(pack: AllowlistPack, text: str) -> IntentMatch | None:
    """The single best-scoring intent, or None when nothing in the allowlist matched."""
    matches = score_intents(pack, text)
    return matches[0] if matches else None
