"""The procedure and next-best-step engine: pure, frozen, replayable, and never a model.

This is the heart of agent-assist. Given a procedure pack (states, exit criteria, allowed
transitions, required evidence) and the live transcript, it decides exactly two things:

1. **where the contact has got to**, by matching the pack's lexicon against what was actually
   said and walking the allowed transitions; and
2. **the single next best step**, which is a sentence the pack's author wrote, selected by this
   code, cited to the state it came from.

**The model never picks a step.** It does not rank steps, it does not paraphrase them and it
does not see this function's inputs. A copilot that let a language model choose the next step in
a regulated procedure would have moved the procedure out of the reviewed artifact and into a
sampler, and no amount of prompt engineering puts it back.

Determinism is structural, not aspirational:

* the only clock is the caller's ``as_of``;
* phrase matching is the kit's (normalisation, spans and ordering all live in one place);
* transition selection is a written rule over ordered lists, not a search;
* the walk is bounded by the number of states, so a pack with a cycle terminates rather than
  spinning. A cycle is legal policy (a state you may return to); an unbounded walk is not.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from speech_lexicon_kit import ChannelRole, LexiconHit, Transcript, find_hits, ordered_hit_chain

from .kernel import Citation
from .models import NextBestStep, ProcedureProgress
from .packs import ProcedurePack, ProcedureState

__all__ = ["ProcedureEngineError", "advance", "next_best_step", "replay_carry_over"]


class ProcedureEngineError(ValueError):
    """The engine was asked to reason about a contact it has no pack for."""


def _hits_after(hits: Sequence[LexiconHit], anchor: LexiconHit | None) -> tuple[LexiconHit, ...]:
    """Hits that begin strictly after ``anchor`` ends, so one utterance cannot serve two states."""
    if anchor is None:
        return tuple(hits)
    return tuple(hit for hit in hits if hit.strictly_after(anchor))


def _usable(hits: Sequence[LexiconHit], state: ProcedureState) -> tuple[LexiconHit, ...]:
    """Only the speaker the state names counts as evidence for it.

    A customer saying "you should tell me this call is recorded" is not the agent making the
    disclosure, and a state whose evidence could come from either speaker is a state a caller
    can satisfy on the agent's behalf.
    """
    if state.role is ChannelRole.UNKNOWN:
        return tuple(hits)
    return tuple(hit for hit in hits if hit.role is state.role)


def _citation(pack: ProcedurePack, state: ProcedureState, hit: LexiconHit | None) -> Citation:
    """Cite the evidence where there is some, and the pack clause where there is not."""
    if hit is None:
        return Citation(
            source_id=f"pack:{pack.pack_id}#{state.state_id}",
            title=state.title,
            snippet=state.instruction,
        )
    return Citation(
        source_id=f"turn:{hit.turn_index}:{hit.char_start}-{hit.char_end}",
        title=f"{state.title} ({hit.entry_id})",
        snippet=hit.matched_text,
    )


def _next_state(state: ProcedureState, completed: Sequence[str]) -> str | None:
    """Pick the transition to take, by a written rule rather than a search.

    Transitions are ordered by precedence in the pack, so the rule is: take the first target
    that has not already been completed. That makes a linear procedure walk in the order it was
    written, lets a pack express a legal loop (a target that HAS been completed is skipped
    rather than re-entered), and never depends on hit ordering, which is what would make the
    branch choice implicitly model-shaped.
    """
    for target in state.transitions:
        if target not in completed:
            return target
    return None


def advance(
    pack: ProcedurePack,
    transcript: Transcript,
    *,
    as_of: datetime,
    resume_from: Sequence[str] = (),
) -> ProcedureProgress:
    """Walk the procedure as far as the transcript evidences, and stop at the first gap.

    ``resume_from`` is the carry-over from a handoff: the state ids a previous session had
    already completed. They are marked complete without re-evidencing them, because the
    evidence for them is in the transcript segment the handoff package carried and re-deriving
    it from a truncated transcript would silently un-complete work the customer already did.
    """
    hits = find_hits(transcript, pack.lexicon)
    completed: list[str] = [state_id for state_id in resume_from if state_id in pack.state_ids]
    citations: list[Citation] = []
    satisfied: list[str] = []
    anchor: LexiconHit | None = None
    entered: list[tuple[str, int | None]] = []
    current = _resume_state(pack, completed)

    # Bounded by the state count: every iteration either completes a state (which the
    # not-already-completed transition rule cannot repeat) or returns.
    for _ in range(len(pack.states) + 1):
        state = pack.state(current)
        entry_anchor = anchor
        available = _hits_after(_usable(hits, state), anchor)
        chain = ordered_hit_chain(available, state.exit_criteria)
        if chain is None:
            return ProcedureProgress(
                pack_id=pack.pack_id,
                state_id=current,
                completed_state_ids=tuple(completed),
                satisfied_evidence=tuple(satisfied),
                missing_evidence=_missing(state, available),
                as_of=as_of,
                citations=tuple(citations) or (_citation(pack, state, None),),
                entered_ms=(*entered, (current, _entry_ms(entry_anchor, transcript))),
                complete=False,
            )
        for hit in chain:
            satisfied.append(hit.entry_id)
            citations.append(_citation(pack, state, hit))
        if chain:
            anchor = chain[-1]
        if current not in completed:
            completed.append(current)
        entered.append((current, _entry_ms(entry_anchor, transcript)))
        following = _next_state(state, completed)
        if following is None:
            return ProcedureProgress(
                pack_id=pack.pack_id,
                state_id=current,
                completed_state_ids=tuple(completed[:-1]),
                satisfied_evidence=tuple(satisfied),
                missing_evidence=(),
                as_of=as_of,
                citations=tuple(citations),
                entered_ms=tuple(entered),
                complete=True,
            )
        current = following

    raise ProcedureEngineError(  # pragma: no cover - the loop bound makes this unreachable
        f"procedure pack {pack.pack_id}: the walk did not terminate within its state count"
    )


def _resume_state(pack: ProcedurePack, completed: Sequence[str]) -> str:
    """Where a resumed walk starts: after the last carried state, or at the pack's beginning."""
    if not completed:
        return pack.initial_state
    following = _next_state(pack.state(completed[-1]), completed)
    return following if following is not None else completed[-1]


def _entry_ms(anchor: LexiconHit | None, transcript: Transcript) -> int | None:
    """When a state was entered, in transcript milliseconds.

    The first state is entered when the contact starts, which is millisecond zero rather than
    the first turn's offset: a disclosure due "within 45 seconds of the contact starting" is
    counted from the contact, not from whenever the recogniser first produced a word.
    """
    if anchor is None:
        return 0 if transcript.turns else None
    return anchor.end_ms


def _missing(state: ProcedureState, available: Sequence[LexiconHit]) -> tuple[str, ...]:
    """The evidence this state still lacks, in the order the pack requires it."""
    present = {hit.entry_id for hit in available}
    return tuple(
        entry_id
        for entry_id in (*state.exit_criteria, *state.required_evidence)
        if entry_id not in present
    )


def next_best_step(pack: ProcedurePack, progress: ProcedureProgress) -> NextBestStep:
    """The one instruction to show the agent. Written by the pack, selected by this function."""
    state = pack.state(progress.state_id)
    if progress.complete:
        return NextBestStep(
            state_id=state.state_id,
            instruction="The procedure is complete. Close the contact and confirm next steps.",
            rationale=f"every state of {pack.pack_id} has its required evidence",
            required_evidence=(),
            citations=(_citation(pack, state, None),),
        )
    return NextBestStep(
        state_id=state.state_id,
        instruction=state.instruction,
        rationale=(
            f"state {state.state_id!r} is still missing "
            f"{', '.join(progress.missing_evidence) or 'its evidence'}"
        ),
        required_evidence=progress.missing_evidence,
        citations=(_citation(pack, state, None),),
    )


def replay_carry_over(
    pack: ProcedurePack,
    transcript: Transcript,
    *,
    as_of: datetime,
    carry_over: Sequence[str],
) -> ProcedureProgress:
    """Resume a handed-off contact and prove the resumed state matches what was carried.

    A handoff that loses the state machine's position is a handoff that makes the customer
    repeat themselves, which is the single most common complaint about escalations. So the
    carry-over is replayed through the SAME engine rather than assigned, and the caller can
    compare the result with the package it received.
    """
    return advance(pack, transcript, as_of=as_of, resume_from=carry_over)
