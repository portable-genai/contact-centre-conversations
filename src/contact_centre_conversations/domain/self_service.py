"""Self-service mode: the customer-facing assistant, and everything it is not allowed to do.

Nobody stands between this code and a member of the public, so every decision in it is
deterministic and every one of them fails closed:

* the **intent** is scored by ``intent_engine`` from phrase matches, not by a classifier;
* the **gate** is ``policy_gate``, over allowlists that refuse when empty;
* the **action** is prepared by ``action_engine``, which never executes a consequential one;
* the **handoff** is triggered by ``handoff.decide_trigger``, from facts the other three
  produced.

The model appears exactly once, in the same grounded-suggestion pipeline agent-assist uses, and
only after the gate has ALLOWED the turn. A denied turn never reaches a model at all: there is
nothing for it to helpfully say, and a fluent apology for a refusal is how a customer is talked
out of asking for a person.

Containment is a metric, not a goal pursued in code. Nothing here tries to keep a contact in
self-service; the gate decides, and containment is what we measure afterwards.
"""

from __future__ import annotations

from datetime import datetime

from speech_lexicon_kit import Transcript, find_hits

from ..ports.observability import ObservabilityTracerPort
from ..ports.party_records import PartyRecordsPort
from ..ports.review_router import ReviewRouterPort
from ..ports.tool_catalog import ToolCatalogPort
from . import action_engine, disclosure_engine, handoff, intent_engine, policy_gate
from .contact_kernel import ContactKernel
from .guardrails import degradation_for
from .kernel import Citation, Decision, Severity
from .models import (
    ActionCall,
    ActionOutcome,
    ActionSpec,
    DisclosureReport,
    GateOutcome,
    HandoffPackage,
    HandoffTrigger,
    PolicyVerdict,
    SelfServiceResult,
    TurnSubmission,
)
from .modes import ContactMode
from .packs import PackLibrary
from .procedure_engine import ProcedureEngineError, advance

__all__ = ["SelfServiceService", "SessionState"]

_MODE = ContactMode.SELF_SERVICE

#: The unit of work, named identically on the trace span and in the audit record, so a slow span
#: found in the backend maps to exactly one audit action rather than to a guess.
_ACTION = "self_service.turn"

#: One span per customer turn. STRUCTURAL attributes only: see :meth:`SelfServiceService.handle`.
_HANDLE_SPAN = "contact_centre_conversations.self_service.turn"


class SessionState:
    """The small amount of per-contact state the gate needs, held by the caller.

    Deliberately not persisted inside the domain: a counter of consecutive failures is session
    state, and a service that recomputed it from the store would silently reset it whenever a
    contact was resumed, which is precisely when it matters.
    """

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.verdicts: list[PolicyVerdict] = []

    def observe(self, verdict: PolicyVerdict) -> None:
        self.verdicts.append(verdict)
        if verdict.allowed:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1


class SelfServiceService:
    """Answer one customer turn, or refuse it and fetch a person."""

    def __init__(
        self,
        *,
        kernel: ContactKernel,
        packs: PackLibrary,
        tools: ToolCatalogPort,
        party_records: PartyRecordsPort,
        review_router: ReviewRouterPort,
        tracer: ObservabilityTracerPort,
    ) -> None:
        self._kernel = kernel
        self._packs = packs
        self._tools = tools
        self._parties = party_records
        self._review = review_router
        self._tracer = tracer

    def handle(
        self,
        submission: TurnSubmission,
        *,
        actor: str,
        as_of: datetime,
        session: SessionState | None = None,
        requested_action: str = "",
        parameters: dict[str, str] | None = None,
    ) -> SelfServiceResult:
        """Take one customer turn and produce the whole decision, in one deterministic pass.

        The whole path runs inside ONE span, and that span's attributes are STRUCTURAL only: the
        action, the actor, the tenant and the market. Never the customer's utterance, never the
        matched intent's text, never a contact identifier, a requested action's parameters or any
        part of the reply. A trace backend is not the WORM audit trail: it has no redaction
        stage, no retention rule written against a regulator's requirement, and a far wider read
        audience than the audit store. So anything content-shaped that reaches a span has left
        the boundary ``TurnGuard`` exists to hold, and it has left it silently.
        """
        with self._tracer.span(
            _HANDLE_SPAN,
            action=_ACTION,
            actor=actor,
            tenant=submission.contact.tenant,
            market=submission.contact.market,
        ):
            contact = submission.contact
            state = session or SessionState()
            guarded = self._kernel.accept(submission)
            degradation = degradation_for(_MODE, guarded.screen)
            transcript = self._kernel.transcript(contact)

            allowlist = self._packs.allowlist_for(contact.tenant, contact.market, contact.vertical)
            intent = (
                intent_engine.best_intent(allowlist, guarded.turn.text)
                if allowlist is not None and degradation.allow_model
                else None
            )
            spec = (
                self._packs.action_spec(requested_action, contact.vertical)
                if requested_action
                else None
            )
            verdict = policy_gate.evaluate(
                allowlist,
                tenant=contact.tenant,
                market=contact.market,
                intent=intent,
                as_of=as_of,
                requested_action=requested_action,
                action_spec=spec,
            )
            state.observe(verdict)

            action = self._act(
                verdict,
                contact_id=contact.contact_id,
                tenant=contact.tenant,
                vertical=contact.vertical,
                party_ref=contact.party_ref,
                action_id=requested_action,
                parameters=parameters or {},
                as_of=as_of,
            )

            # Cue lists are PER MARKET policy, so they are looked up per contact rather than
            # bound once at construction: a deployment serving two markets would otherwise apply
            # one market's vulnerability definition to the other's customers.
            cues = self._packs.cues_for(contact.market, contact.vertical)
            trigger = handoff.decide_trigger(
                verdict=verdict,
                consecutive_failures=state.consecutive_failures,
                customer_text=guarded.turn.text,
                escalation_lexicon=cues.escalation if cues else None,
                vulnerability_lexicon=cues.vulnerability if cues else None,
                screen_failure=degradation.outcome if degradation.handoff else None,
                consequential_action=bool(action and action.requires_human_review),
            )

            disclosures = self._disclosures(
                contact.market,
                contact.vertical,
                transcript=transcript,
                as_of=as_of,
                contact_ended=submission.ends_contact,
            )

            suggestion = self._kernel.suggest(
                guarded.turn.text,
                contact=contact,
                mode=_MODE,
                # A denied turn never reaches a model. See the module docstring.
                allow_model=degradation.allow_model and verdict.allowed,
            )

            package = (
                self._package(
                    submission,
                    trigger=trigger,
                    transcript=transcript,
                    verdicts=state.verdicts,
                    as_of=as_of,
                    action_id=requested_action,
                    parameters=parameters or {},
                )
                if trigger is not None
                else None
            )

            requires_review = bool(
                disclosures.requires_human_review
                or (action is not None and action.requires_human_review)
            )

            result = SelfServiceResult(
                contact=contact,
                transcript=transcript,
                screen=guarded.screen,
                verdict=verdict,
                disclosures=disclosures,
                suggestion=suggestion.reply,
                action=action,
                handoff=package,
                requires_human_review=requires_review,
            )

            review_ref = ""
            if requires_review:
                review_ref = self._review.route(result, maker=actor, tenant=contact.tenant)
                if action is not None:
                    action = ActionOutcome(
                        action_id=action.action_id,
                        executed=action.executed,
                        detail=action.detail,
                        reference=action.reference,
                        requires_human_review=action.requires_human_review,
                        review_ref=review_ref,
                    )

            citations: list[Citation] = [*verdict.citations]
            for status in disclosures.missed:
                citations.extend(status.citations)

            self._kernel.record(
                action=_ACTION,
                actor=actor,
                mode=_MODE,
                contact=contact,
                decision=Decision.ESCALATED if requires_review else Decision.ALLOWED,
                severity=_severity(verdict, disclosures),
                summary=_summary(result, suggestion.detail),
                citations=citations,
                timestamp=as_of,
            )

            return SelfServiceResult(
                contact=contact,
                transcript=transcript,
                screen=guarded.screen,
                verdict=verdict,
                disclosures=disclosures,
                suggestion=suggestion.reply,
                action=action,
                handoff=package,
                requires_human_review=requires_review,
                review_ref=review_ref,
            )

    # ------------------------------------------------------------------ parts
    def _act(
        self,
        verdict: PolicyVerdict,
        *,
        contact_id: str,
        tenant: str,
        vertical: str,
        party_ref: str,
        action_id: str,
        parameters: dict[str, str],
        as_of: datetime,
    ) -> ActionOutcome | None:
        """Prepare and, only where permitted, execute. The catalog is asked before anything runs."""
        if not action_id:
            return None
        spec = self._tools.describe(action_id, vertical)
        call = ActionCall(
            action_id=action_id,
            contact_id=contact_id,
            tenant=tenant,
            vertical=vertical,
            party_ref=party_ref,
            parameters=parameters,
        )
        may_execute, provisional = action_engine.decide(
            spec, call, verdict, as_of=as_of, ownership=self._ownership(spec, call)
        )
        if not may_execute:
            return provisional
        return self._tools.execute(call)

    def _ownership(self, spec: ActionSpec | None, call: ActionCall) -> dict[str, bool]:
        """Resolve, per parameter the catalog binds to a party, whether this caller owns it.

        Only the declared ones are asked about, and only when a value was actually supplied: a
        lookup for a parameter nobody sent would be a question about nothing. The port raises
        when it cannot answer, and that propagates rather than becoming a quiet False, because
        "the records system is down" must not read to a customer as "that is not your card".
        """
        if spec is None:
            return {}
        return {
            parameter.name: self._parties.owns(
                party_ref=call.party_ref,
                tenant=call.tenant,
                parameter=parameter.name,
                value=call.parameters[parameter.name],
            )
            for parameter in spec.parameters
            if parameter.binds_to_party and parameter.name in call.parameters
        }

    def _package(
        self,
        submission: TurnSubmission,
        *,
        trigger: HandoffTrigger,
        transcript: Transcript,
        verdicts: list[PolicyVerdict],
        as_of: datetime,
        action_id: str,
        parameters: dict[str, str],
    ) -> HandoffPackage:
        contact = submission.contact
        procedure = self._packs.procedure_for(contact.market, contact.vertical)
        progress = advance(procedure, transcript, as_of=as_of) if procedure is not None else None
        pending = (
            ActionCall(
                action_id=action_id,
                contact_id=contact.contact_id,
                tenant=contact.tenant,
                vertical=contact.vertical,
                party_ref=contact.party_ref,
                parameters=parameters,
            )
            if action_id
            else None
        )
        return handoff.build_package(
            contact,
            trigger=trigger,
            redacted_turns=transcript.turns,
            progress=progress,
            verdicts=verdicts,
            created_at=as_of,
            pending_action=pending,
            citations=progress.citations if progress is not None else (),
        )

    def _disclosures(
        self,
        market: str,
        vertical: str,
        *,
        transcript: Transcript,
        as_of: datetime,
        contact_ended: bool,
    ) -> DisclosureReport:
        pack = self._packs.disclosure_for(market, vertical)
        if pack is None:
            raise ProcedureEngineError(
                f"no disclosure pack is configured for market {market!r} and vertical {vertical!r}"
            )
        procedure = self._packs.procedure_for(market, vertical)
        hits = find_hits(transcript, procedure.lexicon) if procedure is not None else ()
        return disclosure_engine.evaluate_disclosures(
            pack,
            transcript,
            as_of=as_of,
            procedure_hits=hits,
            contact_ended=contact_ended,
        )


def _severity(verdict: PolicyVerdict, disclosures: DisclosureReport) -> Severity:
    if disclosures.missed:
        order = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
        return max((status.severity for status in disclosures.missed), key=order.index)
    if verdict.outcome is GateOutcome.REVIEW:
        return Severity.HIGH
    return Severity.MEDIUM if verdict.denied else Severity.LOW


def _summary(result: SelfServiceResult, suggestion_detail: str) -> str:
    handed = result.handoff.trigger.value if result.handoff else "none"
    acted = result.action.action_id if result.action else "none"
    return (
        f"{result.contact.contact_id}: gate={result.verdict.outcome.value} "
        f"intent={result.verdict.intent_id or 'none'} action={acted} handoff={handed} "
        f"screen={result.screen.outcome.value} suggestion={suggestion_detail}"
    )
