"""The shared kernel both modes stand on: transcripts, the knowledge base, and the audit trail.

E1 is one repository with two separately gated modes, and this module is the "one" part. It owns
the three things neither mode may implement twice:

* **Transcripts.** Turn assembly from stored, redacted turns into a kit ``Transcript``, so the
  agent-assist panel and the self-service session cite the same turn indices and character
  spans, and a handoff between them means something.
* **The knowledge base.** One grounded-suggestion pipeline, so a reply drafted for an agent to
  read out and a reply sent to a customer pass the same grounding checks. The customer-facing
  one is not allowed to be looser because nobody is watching; if anything the argument runs the
  other way, and running one pipeline settles it.
* **The audit trail.** One writer, and every record TAGGED WITH THE MODE that produced it.
  Without the tag, a mode's promotion evidence cannot be separated from the other's, which is
  the whole reason the modes are gated apart.

The mode-specific decisions live in ``assist_service.py`` and ``self_service.py``. Nothing here
knows which mode is calling except as a value it records.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from speech_lexicon_kit import SpeakerTurn, Transcript

from ..ports.audit import AuditSinkPort
from ..ports.contact_store import ContactStorePort
from ..ports.generation import GenerationPort
from ..ports.retrieval import RetrievalPort
from . import suggestions
from .guardrails import GuardedTurn, TurnGuard
from .kernel import AuditEvent, Citation, Decision, Severity
from .models import ContactRef, RetrievedPassage, SuggestedReply, TurnSubmission
from .modes import ContactMode

__all__ = ["ContactKernel", "GroundedSuggestion"]


@dataclass(frozen=True, slots=True)
class GroundedSuggestion:
    """A suggestion attempt and why it produced what it produced.

    The reason is for the operator and the eval, never for a caller to branch on: there is no
    path that uses a discarded draft, so the only consumer of ``detail`` is a log line.
    """

    reply: SuggestedReply | None
    passages: tuple[RetrievedPassage, ...]
    detail: str

    @property
    def suppressed(self) -> bool:
        return self.reply is None


class ContactKernel:
    """Transcripts, grounded drafting and the audit write: shared by both modes."""

    def __init__(
        self,
        *,
        store: ContactStorePort,
        audit: AuditSinkPort,
        guard: TurnGuard,
        retrieval: RetrievalPort,
        generation: GenerationPort,
    ) -> None:
        self._store = store
        self._audit = audit
        self._guard = guard
        self._retrieval = retrieval
        self._generation = generation

    # ------------------------------------------------------------------ turns
    def accept(self, submission: TurnSubmission) -> GuardedTurn:
        """Redact, screen and PERSIST one inbound turn, in that order.

        The turn is stored only after it has been redacted, so the store never holds a raw
        identifier either. A store write is not an audit write: the audit record is separate,
        carries the decision, and is written by the mode service once it HAS a decision.

        **The stored index is the store's position, not the channel's number.** A channel may
        number turns from one, restart them on a resumed session, or skip a number it dropped;
        the kit requires a transcript's indices to equal their positions so that a citation of
        "turn 7" resolves. Renumbering here means one contiguous transcript whatever the channel
        did, and the channel's own number stays on the submission for correlation.
        """
        guarded = self._guard.guard(submission)
        contact = submission.contact
        self._store.create(contact)
        position = len(self._store.turns(contact.contact_id, tenant=contact.tenant))
        turn = replace(guarded.turn, index=position)
        screen = replace(guarded.screen, turn_index=position)
        self._store.append_turn(contact.contact_id, turn, tenant=contact.tenant)
        return GuardedTurn(turn=turn, screen=screen, redacted=guarded.redacted)

    def transcript(self, contact: ContactRef, *, started_at: datetime | None = None) -> Transcript:
        """Assemble the contact's stored, redacted turns into one kit transcript."""
        turns = tuple(self._store.turns(contact.contact_id, tenant=contact.tenant))
        return Transcript(
            transcript_id=contact.contact_id,
            locale=contact.locale,
            turns=turns,
            started_at=started_at,
            engine="contact-centre-conversations",
        )

    # ------------------------------------------------------- grounded drafting
    def suggest(
        self,
        text: str,
        *,
        contact: ContactRef,
        mode: ContactMode,
        allow_model: bool,
    ) -> GroundedSuggestion:
        """Retrieve, then draft, then validate. Any failure at any stage produces no suggestion.

        The generation port is not called at all when retrieval came back empty. That ordering
        is the control: a model asked to answer with no passages will answer, and the answer
        will be ungrounded and fluent, which is the worst combination available.
        """
        if not allow_model:
            return GroundedSuggestion(
                reply=None,
                passages=(),
                detail="the guardrail degraded this turn, so no model or knowledge base was called",
            )
        query = suggestions.build_query(
            text,
            market=contact.market,
            locale=contact.locale,
            vertical=contact.vertical,
            mode=mode,
        )
        try:
            passages = tuple(self._retrieval.retrieve(query))
        except Exception as exc:  # noqa: BLE001 - an unreachable index suppresses, never invents
            return GroundedSuggestion(
                reply=None,
                passages=(),
                detail=f"retrieval unavailable ({type(exc).__name__}), so nothing is suggested",
            )
        if not passages:
            return GroundedSuggestion(
                reply=None,
                passages=(),
                detail="empty retrieval: no passage, no suggestion",
            )
        try:
            payload = self._generation.draft(text, passages)
        except Exception as exc:  # noqa: BLE001 - a model failure is silence, not a fallback
            return GroundedSuggestion(
                reply=None,
                passages=passages,
                detail=f"generation unavailable ({type(exc).__name__})",
            )
        reply = suggestions.validate_draft(payload, passages, mode=mode)
        if reply is None:
            return GroundedSuggestion(
                reply=None,
                passages=passages,
                detail="the draft failed schema, citation or grounding validation; discarded",
            )
        return GroundedSuggestion(reply=reply, passages=passages, detail="grounded and cited")

    # ------------------------------------------------------------------ audit
    def record(
        self,
        *,
        action: str,
        actor: str,
        mode: ContactMode,
        contact: ContactRef,
        decision: Decision,
        severity: Severity,
        summary: str,
        citations: Sequence[Citation] = (),
        timestamp: datetime,
    ) -> None:
        """Write one already-redacted audit record, tagged with the mode that produced it.

        ``summary`` must already be redacted. It is not re-redacted here, deliberately: a
        function that scrubbed its own input would let a caller pass raw text and stay green,
        and the caller is where the raw text actually is.
        """
        self._audit.record(
            AuditEvent(
                action=action,
                actor=actor,
                decision=decision,
                severity=severity,
                redacted_summary=summary,
                citations=tuple(citations),
                timestamp=timestamp,
                mode=mode.value,
                contact_id=contact.contact_id,
                tenant=contact.tenant,
            )
        )

    @staticmethod
    def redacted_turns(transcript: Transcript) -> tuple[SpeakerTurn, ...]:
        """The transcript's turns, which are already redacted because nothing else is stored."""
        return transcript.turns
