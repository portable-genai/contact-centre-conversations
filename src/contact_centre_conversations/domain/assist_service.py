"""Agent-assist mode: the whisper copilot beside a live human agent.

One turn in, one panel out. Everything consequential in that panel is produced by a
deterministic engine, and the one model-produced element (the suggested reply) is optional,
cited, and suppressed whenever grounding is not available.

What the panel carries and who decided it:

| Panel element        | Decided by                                    |
|----------------------|-----------------------------------------------|
| procedure state      | ``procedure_engine.advance`` (pure)           |
| next best step       | ``procedure_engine.next_best_step`` (pure)    |
| due disclosures      | ``disclosure_engine.evaluate_disclosures``    |
| suggested reply      | the model, validated and discardable          |
| review banner        | a missed disclosure window, deterministically |

A missed disclosure window is the consequential outcome of this mode: it sets
``requires_human_review`` and routes to Hrz7 under rule R8 in the same call that produced it.
Setting the flag is not the escalation; routing is.
"""

from __future__ import annotations

from datetime import datetime

from speech_lexicon_kit import Transcript, find_hits

from ..ports.observability import ObservabilityTracerPort
from ..ports.review_router import ReviewRouterPort
from . import disclosure_engine, escalation, procedure_engine
from .contact_kernel import ContactKernel
from .guardrails import degradation_for
from .kernel import Citation, Decision, Severity
from .models import AssistResult, DisclosureReport, ProcedureProgress, TurnSubmission
from .modes import ContactMode
from .packs import PackLibrary

__all__ = ["AgentAssistService"]

_MODE = ContactMode.AGENT_ASSIST

#: The unit of work, named identically on the trace span and in the audit record, so a slow span
#: found in the backend maps to exactly one audit action rather than to a guess.
_ACTION = "agent_assist.turn"

#: One span per observed turn. STRUCTURAL attributes only: see :meth:`AgentAssistService.observe`.
_OBSERVE_SPAN = "contact_centre_conversations.agent_assist.turn"


class AgentAssistService:
    """Produce the whisper panel for one inbound turn, and route what must be reviewed."""

    def __init__(
        self,
        *,
        kernel: ContactKernel,
        packs: PackLibrary,
        review_router: ReviewRouterPort,
        tracer: ObservabilityTracerPort,
    ) -> None:
        self._kernel = kernel
        self._packs = packs
        self._review = review_router
        self._tracer = tracer

    def observe(self, submission: TurnSubmission, *, actor: str, as_of: datetime) -> AssistResult:
        """Take one turn and return the panel. ``as_of`` is the only clock in the whole path.

        The whole path runs inside ONE span, and that span's attributes are STRUCTURAL only: the
        action, the actor, the tenant and the market. Never the turn text, never the customer's
        utterance, never a contact identifier or any panel content. A trace backend is not the
        WORM audit trail: it has no redaction stage, no retention rule written against a
        regulator's requirement, and a far wider read audience than the audit store. So anything
        content-shaped that reaches a span has left the boundary ``TurnGuard`` exists to hold,
        and it has left it silently.
        """
        with self._tracer.span(
            _OBSERVE_SPAN,
            action=_ACTION,
            actor=actor,
            tenant=submission.contact.tenant,
            market=submission.contact.market,
        ):
            contact = submission.contact
            guarded = self._kernel.accept(submission)
            degradation = degradation_for(_MODE, guarded.screen)
            transcript = self._kernel.transcript(contact)

            procedure = self._packs.procedure_for(contact.market, contact.vertical)
            if procedure is None:
                raise procedure_engine.ProcedureEngineError(
                    f"no procedure pack is configured for market {contact.market!r} and vertical "
                    f"{contact.vertical!r}: an agent-assist panel with no procedure would have "
                    "nothing deterministic to show"
                )
            progress = procedure_engine.advance(procedure, transcript, as_of=as_of)
            next_step = procedure_engine.next_best_step(procedure, progress)

            disclosures = self._disclosures(
                contact.market,
                contact.vertical,
                transcript=transcript,
                progress=progress,
                as_of=as_of,
                contact_ended=submission.ends_contact,
            )

            suggestion = self._kernel.suggest(
                guarded.turn.text,
                contact=contact,
                mode=_MODE,
                allow_model=degradation.allow_model,
            )

            escalations = escalation.reasons_for(degradation=degradation, disclosures=disclosures)
            requires_review = bool(escalations)
            severity = _severity(disclosures)
            citations: list[Citation] = [*next_step.citations]
            for status in disclosures.missed:
                citations.extend(status.citations)

            result = AssistResult(
                contact=contact,
                transcript=transcript,
                screen=guarded.screen,
                progress=progress,
                next_step=next_step,
                disclosures=disclosures,
                suggestion=suggestion.reply,
                requires_human_review=requires_review,
                deterministic_only=not degradation.allow_model,
            )

            review_ref = ""
            if requires_review:
                # Rule R8 on this surface too: the flag and the routing are one act.
                review_ref = self._review.route(result, maker=actor, tenant=contact.tenant)

            self._kernel.record(
                action=_ACTION,
                actor=actor,
                mode=_MODE,
                contact=contact,
                decision=Decision.ESCALATED if requires_review else Decision.ALLOWED,
                severity=severity,
                summary=_summary(result, suggestion.detail),
                citations=citations,
                timestamp=as_of,
            )

            return AssistResult(
                contact=result.contact,
                transcript=result.transcript,
                screen=result.screen,
                progress=result.progress,
                next_step=result.next_step,
                disclosures=result.disclosures,
                suggestion=result.suggestion,
                requires_human_review=result.requires_human_review,
                review_ref=review_ref,
                deterministic_only=result.deterministic_only,
            )

    def _disclosures(
        self,
        market: str,
        vertical: str,
        *,
        transcript: Transcript,
        progress: ProcedureProgress,
        as_of: datetime,
        contact_ended: bool,
    ) -> DisclosureReport:
        pack = self._packs.disclosure_for(market, vertical)
        procedure = self._packs.procedure_for(market, vertical)
        if pack is None:
            # No pack is not "no obligations": it is a market and line of business this
            # deployment was not configured for, and an empty report would look identical to a
            # market with nothing required.
            raise procedure_engine.ProcedureEngineError(
                f"no disclosure pack is configured for market {market!r} and vertical {vertical!r}"
            )
        hits = find_hits(transcript, procedure.lexicon) if procedure is not None else ()
        return disclosure_engine.evaluate_disclosures(
            pack,
            transcript,
            as_of=as_of,
            progress=progress,
            procedure_hits=hits,
            contact_ended=contact_ended,
        )


def _severity(disclosures: DisclosureReport) -> Severity:
    """The band a missed window carries into the audit record, from the pack's own severity."""
    missed = disclosures.missed
    if not missed:
        return Severity.LOW
    order = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
    return max((status.severity for status in missed), key=order.index)


def _summary(result: AssistResult, suggestion_detail: str) -> str:
    """The audit line. Facts from the artifacts, never the turn text, so nothing raw is copied."""
    return (
        f"{result.contact.contact_id}: state={result.progress.state_id} "
        f"next={result.next_step.state_id} "
        f"due={len(result.disclosures.due)} missed={len(result.disclosures.missed)} "
        f"screen={result.screen.outcome.value} suggestion={suggestion_detail}"
    )
