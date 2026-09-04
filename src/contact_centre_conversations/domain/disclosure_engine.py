"""Disclosure-reminder timing: deterministic from turn offsets and elapsed windows.

A required disclosure has three parts, and only the first is about words: the WORDING (which the
kit matches), the TRIGGER (what starts the clock) and the WINDOW (how long the clock runs). This
module owns the last two and asks the kit for the first, so "the recording notice was given
within 45 seconds of the contact starting" is a computation over integers rather than a judgement
call by a model.

Four outcomes, and the difference between the last two is the honest part:

* ``SATISFIED`` : the wording, from the right speaker, inside the window. * ``PENDING``   :
  triggered, still inside the window, contact still live. This is what the whisper panel shows as a
  reminder. * ``MISSED``    : the window closed and nothing matched. Consequential: the report sets
  ``requires_human_review`` and the caller routes it to human-review-console under rule R8. A missed
  disclosure is a regulatory event, not a UI state. * ``UNVERIFIABLE`` : the transcript carries no
  timings at all and the pack sets a timed window, so nothing here can answer the question.
  Reporting SATISFIED or MISSED from an absent clock would be inventing evidence in whichever
  direction the default happened to point.

**A reminder never fires without its trigger.** ``due_from_ms`` is None until the trigger event
is evidenced, and a status with no ``due_from_ms`` is never due. That property is what the
reminder-timeliness metric measures in both directions.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from speech_lexicon_kit import ChannelRole, LexiconHit, Transcript, find_hits

from .kernel import Citation
from .models import DisclosureReport, DisclosureState, DisclosureStatus, ProcedureProgress
from .packs import TRIGGER_CONTACT_START, DisclosurePack, DisclosureSpec

__all__ = ["evaluate_disclosures"]

_TRIGGER_STATE = "procedure_state:"
_TRIGGER_LEXICON = "lexicon:"


def _hit_window(transcript: Transcript, hit: LexiconHit) -> tuple[int | None, int | None]:
    """When a phrase hit happened, falling back to its TURN's bounds when words are untimed.

    The kit resolves a span's timing from word offsets and returns None rather than substituting
    the turn's bounds, because for an adherence deadline a made-up timestamp is worse than an
    unverifiable one. That is right for the kit and it is not the whole answer here, because a
    contact-centre transcript very often carries turn timings and no word timings, and refusing
    to answer at all would make every timed disclosure unverifiable in the common case.

    So the fallback is deliberately the CONSERVATIVE bound: the phrase cannot have finished
    later than the turn it is in finished, and cannot have started earlier than the turn started.
    Using the turn's END as the completion time can only make a disclosure look LATER than it
    was, so it can produce a MISSED verdict that a word-timed transcript would have called
    satisfied, and it can never produce a false SATISFIED. The error direction is the one that
    sends a case to a human.
    """
    if hit.start_ms is not None and hit.end_ms is not None:
        return hit.start_ms, hit.end_ms
    turn = transcript.turn(hit.turn_index)
    return (
        hit.start_ms if hit.start_ms is not None else turn.start_ms,
        hit.end_ms if hit.end_ms is not None else turn.end_ms,
    )


def _contact_ms(transcript: Transcript) -> int | None:
    """The latest millisecond the transcript can speak for, or None when it carries no timings."""
    offsets = [turn.end_ms for turn in transcript.turns if turn.end_ms is not None]
    if offsets:
        return max(offsets)
    return transcript.audio_duration_ms


def _trigger_ms(
    spec: DisclosureSpec,
    *,
    progress: ProcedureProgress | None,
    procedure_hits: Sequence[LexiconHit],
    transcript: Transcript,
) -> int | None:
    """When this disclosure's clock started, or None when its trigger has not fired."""
    if spec.trigger_event == TRIGGER_CONTACT_START:
        return 0 if transcript.turns else None
    if spec.trigger_event.startswith(_TRIGGER_STATE):
        state_id = spec.trigger_event[len(_TRIGGER_STATE) :]
        return progress.entry_ms(state_id) if progress is not None else None
    entry_id = spec.trigger_event[len(_TRIGGER_LEXICON) :]
    candidates = [hit for hit in procedure_hits if hit.entry_id == entry_id]
    if not candidates:
        return None
    earliest = min(candidates, key=lambda hit: hit.position)
    return earliest.end_ms if earliest.end_ms is not None else 0


def _satisfying_hit(
    spec: DisclosureSpec,
    hits: Sequence[LexiconHit],
    *,
    trigger_ms: int | None,
    transcript: Transcript,
) -> LexiconHit | None:
    """The earliest hit from the right speaker that is not before the trigger.

    "Not before the trigger" is deliberately inclusive of hits with no timing: a transcript
    without milliseconds still evidences that the words were said, and the window question is
    answered separately (as UNVERIFIABLE) rather than by silently discarding the evidence.
    """
    eligible = [
        hit
        for hit in hits
        if hit.entry_id == spec.disclosure_id
        and (spec.role is ChannelRole.UNKNOWN or hit.role is spec.role)
        and _not_before(trigger_ms, _hit_window(transcript, hit)[0])
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda hit: hit.position)


def _not_before(trigger_ms: int | None, hit_start_ms: int | None) -> bool:
    """A hit counts unless it demonstrably happened before the window opened."""
    return trigger_ms is None or hit_start_ms is None or hit_start_ms >= trigger_ms


def _citation(pack: DisclosurePack, spec: DisclosureSpec, hit: LexiconHit | None) -> Citation:
    if hit is None:
        return Citation(
            source_id=f"pack:{pack.pack_id}#{spec.disclosure_id}",
            title=f"{spec.disclosure_id} ({pack.jurisdiction})",
            snippet=spec.required_phrase,
        )
    return Citation(
        source_id=f"turn:{hit.turn_index}:{hit.char_start}-{hit.char_end}",
        title=f"{spec.disclosure_id} evidenced",
        snippet=hit.matched_text,
    )


def _state_of(
    *,
    hit_end_ms: int | None,
    hit_found: bool,
    trigger_ms: int | None,
    due_by_ms: int | None,
    now_ms: int | None,
    contact_ended: bool,
) -> DisclosureState:
    """The whole timing decision, in one place, over integers."""
    if trigger_ms is None:
        # The clock never started. Not satisfied, not missed: nothing was required yet.
        return DisclosureState.PENDING
    if hit_found:
        if due_by_ms is None:
            return DisclosureState.SATISFIED
        if hit_end_ms is None:
            # The words are evidenced but the transcript cannot say WHEN, at the turn level
            # either, and this pack asked a timing question. Claiming an answer would be
            # inventing the missing clock.
            return DisclosureState.UNVERIFIABLE
        return DisclosureState.SATISFIED if hit_end_ms <= due_by_ms else DisclosureState.MISSED
    if contact_ended:
        return DisclosureState.MISSED
    if due_by_ms is None:
        return DisclosureState.PENDING
    if now_ms is None:
        return DisclosureState.UNVERIFIABLE
    return DisclosureState.MISSED if now_ms > due_by_ms else DisclosureState.PENDING


def evaluate_disclosures(
    pack: DisclosurePack,
    transcript: Transcript,
    *,
    as_of: datetime,
    progress: ProcedureProgress | None = None,
    procedure_hits: Sequence[LexiconHit] = (),
    contact_ended: bool = False,
) -> DisclosureReport:
    """Evaluate every disclosure this market requires, at one explicit ``as_of``.

    ``contact_ended`` is the caller's fact, not this module's guess: only the channel knows that
    the customer hung up. It is the difference between a reminder still worth showing and a
    window that closed unsatisfied, which is why it is an argument rather than an inference from
    the transcript being quiet.
    """
    hits = find_hits(transcript, pack.lexicon)
    now_ms = _contact_ms(transcript)
    statuses: list[DisclosureStatus] = []
    for spec in pack.disclosures:
        trigger_ms = _trigger_ms(
            spec, progress=progress, procedure_hits=procedure_hits, transcript=transcript
        )
        due_by_ms = (
            trigger_ms + spec.within_ms
            if trigger_ms is not None and spec.within_ms is not None
            else None
        )
        hit = _satisfying_hit(spec, hits, trigger_ms=trigger_ms, transcript=transcript)
        hit_end_ms = _hit_window(transcript, hit)[1] if hit is not None else None
        state = _state_of(
            hit_end_ms=hit_end_ms,
            hit_found=hit is not None,
            trigger_ms=trigger_ms,
            due_by_ms=due_by_ms,
            now_ms=now_ms,
            contact_ended=contact_ended,
        )
        statuses.append(
            DisclosureStatus(
                disclosure_id=spec.disclosure_id,
                state=state,
                severity=spec.severity,
                jurisdiction=pack.jurisdiction,
                due_from_ms=trigger_ms,
                due_by_ms=due_by_ms,
                satisfied_at_ms=hit_end_ms,
                reminder_text=spec.reminder,
                citations=(_citation(pack, spec, hit),),
            )
        )
    return DisclosureReport(
        pack_id=pack.pack_id,
        market=pack.market,
        as_of=as_of,
        statuses=tuple(statuses),
    )
