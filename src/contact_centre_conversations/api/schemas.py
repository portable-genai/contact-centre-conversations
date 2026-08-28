"""API request/response schemas (Pydantic) mapped to and from the pure-domain models.

Two mode-shaped responses, deliberately not merged into one. The agent-assist panel and the
self-service reply carry different things and are gated separately, and a single response type
carrying every field of both would let a client written for one mode silently read the other's.
"""

from __future__ import annotations

from pydantic import BaseModel

from ..domain.models import (
    AssistResult,
    DisclosureReport,
    HandoffPackage,
    PolicyVerdict,
    SelfServiceResult,
    SuggestedReply,
)


class CitationModel(BaseModel):
    source_id: str
    title: str
    snippet: str = ""


class TurnRequest(BaseModel):
    """One inbound turn. The actor is NOT here: identity is resolved server side."""

    contact_id: str
    market: str
    locale: str
    #: The line of business this contact is handled under. Required, and carried like the
    #: market: it selects which reviewed packs apply, and no default can guess it.
    vertical: str
    text: str
    index: int = 0
    speaker_id: str = "customer"
    role: str = "customer"
    start_ms: int | None = None
    end_ms: int | None = None
    ends_contact: bool = False
    channel: str = "voice"
    #: Self-service only: the action the customer's turn is asking for, if any.
    requested_action: str = ""
    parameters: dict[str, str] = {}


class SuggestionModel(BaseModel):
    text: str
    citations: list[CitationModel] = []
    passage_ids: list[str] = []

    @classmethod
    def of(cls, reply: SuggestedReply | None) -> SuggestionModel | None:
        if reply is None:
            return None
        return cls(
            text=reply.text,
            citations=[_citation(c) for c in reply.citations],
            passage_ids=list(reply.passage_ids),
        )


class DisclosureModel(BaseModel):
    disclosure_id: str
    state: str
    severity: str
    jurisdiction: str
    due_from_ms: int | None = None
    due_by_ms: int | None = None
    reminder_text: str = ""


class NextStepModel(BaseModel):
    state_id: str
    instruction: str
    rationale: str
    required_evidence: list[str] = []
    citations: list[CitationModel] = []


class AssistResponse(BaseModel):
    """The agent-assist whisper panel."""

    mode: str
    contact_id: str
    state_id: str
    completed_state_ids: list[str] = []
    next_step: NextStepModel
    disclosures: list[DisclosureModel] = []
    due_disclosure_ids: list[str] = []
    missed_disclosure_ids: list[str] = []
    suggestion: SuggestionModel | None = None
    screen: str = ""
    deterministic_only: bool = False
    requires_human_review: bool = False
    review_ref: str = ""

    @classmethod
    def from_domain(cls, result: AssistResult) -> AssistResponse:
        return cls(
            mode=result.mode.value,
            contact_id=result.contact.contact_id,
            state_id=result.progress.state_id,
            completed_state_ids=list(result.progress.completed_state_ids),
            next_step=NextStepModel(
                state_id=result.next_step.state_id,
                instruction=result.next_step.instruction,
                rationale=result.next_step.rationale,
                required_evidence=list(result.next_step.required_evidence),
                citations=[_citation(c) for c in result.next_step.citations],
            ),
            disclosures=_disclosures(result.disclosures),
            due_disclosure_ids=[s.disclosure_id for s in result.disclosures.due],
            missed_disclosure_ids=[s.disclosure_id for s in result.disclosures.missed],
            suggestion=SuggestionModel.of(result.suggestion),
            screen=result.screen.outcome.value,
            deterministic_only=result.deterministic_only,
            requires_human_review=result.requires_human_review,
            review_ref=result.review_ref,
        )


class GateReasonModel(BaseModel):
    code: str
    outcome: str
    detail: str


class VerdictModel(BaseModel):
    outcome: str
    intent_id: str = ""
    action_id: str = ""
    confidence: float = 0.0
    confidence_floor: float = 0.0
    reasons: list[GateReasonModel] = []

    @classmethod
    def of(cls, verdict: PolicyVerdict) -> VerdictModel:
        return cls(
            outcome=verdict.outcome.value,
            intent_id=verdict.intent_id,
            action_id=verdict.action_id,
            confidence=verdict.confidence,
            confidence_floor=verdict.confidence_floor,
            reasons=[
                GateReasonModel(code=r.code, outcome=r.outcome.value, detail=r.detail)
                for r in verdict.reasons
            ],
        )


class ActionModel(BaseModel):
    action_id: str
    executed: bool
    detail: str
    reference: str = ""
    requires_human_review: bool = False
    review_ref: str = ""


class HandoffModel(BaseModel):
    trigger: str
    summary: str
    procedure_state_id: str = ""
    carry_over_state_ids: list[str] = []
    turn_count: int = 0

    @classmethod
    def of(cls, package: HandoffPackage | None) -> HandoffModel | None:
        if package is None:
            return None
        return cls(
            trigger=package.trigger.value,
            summary=package.summary,
            procedure_state_id=package.procedure_state_id,
            carry_over_state_ids=list(package.carry_over_state_ids),
            turn_count=len(package.turns),
        )


class SelfServiceResponse(BaseModel):
    """The customer-facing reply, its gate verdict, and the handoff banner when one fires."""

    mode: str
    contact_id: str
    verdict: VerdictModel
    disclosures: list[DisclosureModel] = []
    suggestion: SuggestionModel | None = None
    action: ActionModel | None = None
    handoff: HandoffModel | None = None
    screen: str = ""
    contained: bool = False
    requires_human_review: bool = False
    review_ref: str = ""

    @classmethod
    def from_domain(cls, result: SelfServiceResult) -> SelfServiceResponse:
        return cls(
            mode=result.mode.value,
            contact_id=result.contact.contact_id,
            verdict=VerdictModel.of(result.verdict),
            disclosures=_disclosures(result.disclosures),
            suggestion=SuggestionModel.of(result.suggestion),
            action=(
                ActionModel(
                    action_id=result.action.action_id,
                    executed=result.action.executed,
                    detail=result.action.detail,
                    reference=result.action.reference,
                    requires_human_review=result.action.requires_human_review,
                    review_ref=result.action.review_ref,
                )
                if result.action is not None
                else None
            ),
            handoff=HandoffModel.of(result.handoff),
            screen=result.screen.outcome.value,
            contained=result.contained,
            requires_human_review=result.requires_human_review,
            review_ref=result.review_ref,
        )


class ModeStatus(BaseModel):
    mode: str
    enabled: bool
    promotion_bundle: str = ""


class HealthResponse(BaseModel):
    status: str
    profile: str
    region: str
    modes: list[ModeStatus] = []


def _citation(citation: object) -> CitationModel:
    return CitationModel(
        source_id=getattr(citation, "source_id", ""),
        title=getattr(citation, "title", ""),
        snippet=getattr(citation, "snippet", ""),
    )


def _disclosures(report: DisclosureReport) -> list[DisclosureModel]:
    return [
        DisclosureModel(
            disclosure_id=status.disclosure_id,
            state=status.state.value,
            severity=status.severity.value,
            jurisdiction=status.jurisdiction,
            due_from_ms=status.due_from_ms,
            due_by_ms=status.due_by_ms,
            reminder_text=status.reminder_text,
        )
        for status in report.statuses
    ]
