"""Vertical artifact models: the request and result types E1 reasons over.

The artifacts THIS vertical produces, as opposed to the vertical-neutral machinery in
``kernel.py``. Speech and transcript primitives are NOT redeclared here: ``speech-lexicon-kit``
owns ``Transcript``, ``SpeakerTurn``, ``WordOffset``, ``ChannelRole``, ``RedactionSpan`` and
``LexiconHit``, so a citation of "turn 7, characters 12 to 34" means the same thing in this repo
and in every other repo that consumes the kit. Re-implementing them here is the drift this
programme cut a shared kit to prevent.

Everything below is a frozen dataclass over the standard library and those kit types. No web
framework, no cloud SDK, no clock: any type that needs a time carries an explicit ``as_of``
handed in by the caller, so a replay of the same contact produces the same artifacts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from hex_service_kit.enums import LenientStrEnum
from speech_lexicon_kit import ChannelRole, LexiconHit, RedactionSpan, SpeakerTurn, Transcript

from .kernel import Citation, Severity
from .modes import ContactMode

__all__ = [
    "AUDIENCE_PUBLIC",
    "AUDIENCE_INTERNAL",
    "AUDIENCES",
    "ActionCall",
    "ActionOutcome",
    "ActionSpec",
    "AssistResult",
    "ContactChannel",
    "ContactRef",
    "DisclosureReport",
    "DisclosureState",
    "DisclosureStatus",
    "GateOutcome",
    "GateReason",
    "HandoffPackage",
    "HandoffTrigger",
    "IntentMatch",
    "NextBestStep",
    "ParameterSpec",
    "PolicyVerdict",
    "ProcedureProgress",
    "RetrievalQuery",
    "ReviewableResult",
    "RetrievedPassage",
    "ScreenOutcome",
    "ScreenResult",
    "SelfServiceResult",
    "SuggestedReply",
    "TurnSubmission",
]


# --------------------------------------------------------------------------------------- #
# Taxonomies
# --------------------------------------------------------------------------------------- #
class ContactChannel(LenientStrEnum):
    """The medium a contact runs on.

    Voice and chat differ only in how turns arrive (a streaming recogniser versus a message
    channel), and every engine below reads turns rather than audio, so the medium is a label
    carried into the audit record rather than a branch in any decision.
    """

    VOICE = "voice"
    CHAT = "chat"


#: Written for the customer: publishable wording a person may be told and could look up.
AUDIENCE_PUBLIC = "public"
#: Written for staff: handling rules, thresholds and internal procedure. Never quoted outward.
#: It is the DEFAULT for a passage that does not say, because a corpus row nobody classified is
#: the one to keep inside, and the loader refuses such a row anyway.
AUDIENCE_INTERNAL = "internal"
#: Every audience a passage may declare. An unknown value is a corpus error, not a third policy.
AUDIENCES: tuple[str, ...] = (AUDIENCE_PUBLIC, AUDIENCE_INTERNAL)


class GateOutcome(LenientStrEnum):
    """The self-service policy gate's verdict, ordered least to most restrictive."""

    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


#: Verdict severity, so composition can pick the most restrictive outcome (worst wins).
GATE_RANK: Mapping[GateOutcome, int] = {
    GateOutcome.ALLOW: 0,
    GateOutcome.REVIEW: 1,
    GateOutcome.DENY: 2,
}


class DisclosureState(LenientStrEnum):
    """Where one required disclosure stands at a given ``as_of``."""

    #: Its trigger has not fired yet, or the window is still open and the contact is live.
    PENDING = "pending"
    #: The required wording (or an accepted paraphrase) was said inside the window.
    SATISFIED = "satisfied"
    #: The window closed with nothing matching. Consequential: routes to human review.
    MISSED = "missed"
    #: The transcript cannot answer the question (no timings at all), so nothing is claimed.
    UNVERIFIABLE = "unverifiable"


class ScreenOutcome(LenientStrEnum):
    """What the guardrail screen said about one inbound turn."""

    CLEAN = "clean"
    BLOCKED = "blocked"
    #: The screen could not be reached or refused. Fails CLOSED at the caller, never ignored.
    UNAVAILABLE = "unavailable"


class HandoffTrigger(LenientStrEnum):
    """Why a contact left self-service for a human. Deterministic, never model-decided."""

    GATE_DENIAL = "gate_denial"
    REPEATED_FAILED_INTENT = "repeated_failed_intent"
    CUSTOMER_REQUEST = "customer_request"
    VULNERABILITY = "vulnerability"
    #: The guardrail BLOCKED the turn (an injection or an abuse pattern). Distinct from the
    #: next one on purpose: "we refused what you sent" and "we could not check what you sent"
    #: are different facts, and a receiving agent needs to be told which happened.
    SCREEN_BLOCKED = "screen_blocked"
    SCREEN_UNAVAILABLE = "screen_unavailable"
    CONSEQUENTIAL_ACTION = "consequential_action"


# --------------------------------------------------------------------------------------- #
# The contact and its turns
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ContactRef:
    """Identity of one contact: who it belongs to, where it runs, and under which mode.

    ``vertical`` is the line of business the contact is being handled under, and it is a
    REQUIRED field with no default. A market alone does not select a policy: a bank and an
    insurer both operate in SG, their procedures and disclosures are different reviewed
    artifacts, and packs are selected by ``(market, vertical)``. A default here would pick one
    line of business for a contact nobody classified, which is the silent-shadowing failure the
    pack key exists to prevent.
    """

    contact_id: str
    tenant: str
    market: str
    locale: str
    vertical: str
    mode: ContactMode
    channel: ContactChannel = ContactChannel.VOICE
    #: WHO this contact is about: the party whose records may be reached on it. Empty means
    #: nobody has been identified yet, which is a real state and not a missing value: a contact
    #: begins before anyone is verified. An unidentified party owns nothing, so every ownership
    #: check fails closed until the channel fills this in. Note that this records who the
    #: channel SAYS it is speaking to; authenticating that claim on the customer-facing channel
    #: is a separate concern and is not solved here.
    party_ref: str = ""

    def __post_init__(self) -> None:
        for name in ("contact_id", "tenant", "market", "locale", "vertical"):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"ContactRef.{name} must not be empty")


@dataclass(frozen=True, slots=True)
class TurnSubmission:
    """One inbound turn, as it arrives, BEFORE redaction and BEFORE any screen.

    ``text`` is raw. Nothing may hand this object to a model or a knowledge base: the guardrail
    pipeline converts it to a redacted, screened :class:`~speech_lexicon_kit.SpeakerTurn` first,
    and that is the only form the rest of the service sees.
    """

    contact: ContactRef
    index: int
    speaker_id: str
    role: ChannelRole
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    #: Set by the channel when the customer's turn is the last one of the contact.
    ends_contact: bool = False


@dataclass(frozen=True, slots=True)
class ScreenResult:
    """The guardrail verdict for one turn, plus the redaction that preceded it."""

    outcome: ScreenOutcome
    turn_index: int
    detail: str = ""
    categories: tuple[str, ...] = ()
    redactions: tuple[RedactionSpan, ...] = ()

    @property
    def safe_for_model(self) -> bool:
        """Only a CLEAN screen may reach a generation or retrieval port."""
        return self.outcome == ScreenOutcome.CLEAN


# --------------------------------------------------------------------------------------- #
# Procedure state and the next best step
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ProcedureProgress:
    """Where the contact has reached in its procedure, and what evidence carried it there."""

    pack_id: str
    state_id: str
    completed_state_ids: tuple[str, ...]
    satisfied_evidence: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    as_of: datetime
    citations: tuple[Citation, ...] = ()
    #: ``(state_id, entry offset in transcript milliseconds)`` for every state the walk entered.
    #: A tuple of pairs rather than a mapping so the whole artifact stays frozen and comparable,
    #: which is what makes the handoff carry-over replay test an equality assertion.
    entered_ms: tuple[tuple[str, int | None], ...] = ()
    complete: bool = False

    @property
    def carry_over(self) -> tuple[str, ...]:
        """The COMPLETED state ids a resumed engine replays to arrive back here.

        Deliberately NOT including :attr:`state_id`. The current state is where the contact IS,
        not something it finished, and carrying it as complete makes the resumed engine skip
        past it: the receiving agent would be told to do the step after the one nobody did. The
        current state travels separately on the handoff package, and the replay test asserts the
        resumed walk lands on it.
        """
        return self.completed_state_ids

    def entry_ms(self, state_id: str) -> int | None:
        """When ``state_id`` was entered, or None when the walk never reached it."""
        for entered, offset in self.entered_ms:
            if entered == state_id:
                return offset
        return None

    @property
    def reached_state_ids(self) -> tuple[str, ...]:
        return tuple(state_id for state_id, _ in self.entered_ms)


@dataclass(frozen=True, slots=True)
class NextBestStep:
    """The single next action the agent should take. Chosen by the engine, never by a model."""

    state_id: str
    instruction: str
    rationale: str
    required_evidence: tuple[str, ...] = ()
    citations: tuple[Citation, ...] = ()


# --------------------------------------------------------------------------------------- #
# Disclosures
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DisclosureStatus:
    """One required disclosure's state, with the timing that decided it."""

    disclosure_id: str
    state: DisclosureState
    severity: Severity
    jurisdiction: str
    #: When the window opens, in transcript milliseconds. None when the trigger has not fired.
    due_from_ms: int | None = None
    #: When the window closes. None when the pack sets no deadline.
    due_by_ms: int | None = None
    satisfied_at_ms: int | None = None
    reminder_text: str = ""
    citations: tuple[Citation, ...] = ()

    @property
    def is_due(self) -> bool:
        """A reminder is worth showing only for a triggered, still-unsatisfied disclosure."""
        return self.state == DisclosureState.PENDING and self.due_from_ms is not None


@dataclass(frozen=True, slots=True)
class DisclosureReport:
    """Every required disclosure for this market, at one explicit ``as_of``."""

    pack_id: str
    market: str
    as_of: datetime
    statuses: tuple[DisclosureStatus, ...] = ()

    @property
    def due(self) -> tuple[DisclosureStatus, ...]:
        return tuple(s for s in self.statuses if s.is_due)

    @property
    def missed(self) -> tuple[DisclosureStatus, ...]:
        return tuple(s for s in self.statuses if s.state == DisclosureState.MISSED)

    @property
    def requires_human_review(self) -> bool:
        """A window that closed unsatisfied is consequential: it routes to Hrz7 under R8."""
        return bool(self.missed)


# --------------------------------------------------------------------------------------- #
# Retrieval and grounded suggestion (the Hrz2 governed-RAG shape)
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """Query in. The shape is Hrz2's governed-RAG port shape, kept identical on purpose."""

    text: str
    top_k: int = 5
    #: Structured filters the adapter resolves (for example ``{"market": "SG"}``).
    filters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    """Cited passages out. Every passage carries the citation a claim will be traced through.

    ``audience`` says who the passage was written FOR. A knowledge base serving a contact centre
    holds both halves: what a customer may be told, and how staff are meant to handle it. They
    read alike and they are not alike, so which mode may ground a reply in which passage is a
    property of the passage rather than a matter of phrasing the prompt carefully.
    """

    text: str
    citation: Citation
    score: float = 0.0
    audience: str = AUDIENCE_INTERNAL


@dataclass(frozen=True, slots=True)
class SuggestedReply:
    """A drafted reply, admissible only because every sentence traces to a retrieved passage."""

    text: str
    citations: tuple[Citation, ...]
    passage_ids: tuple[str, ...]
    #: The mode the suggestion was drafted for, carried into the audit record.
    mode: ContactMode = ContactMode.AGENT_ASSIST


# --------------------------------------------------------------------------------------- #
# The self-service policy gate
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class IntentMatch:
    """A deterministic intent score: matched phrase coverage, never a model's confidence."""

    intent_id: str
    confidence: float
    matched_phrases: tuple[str, ...] = ()
    hits: tuple[LexiconHit, ...] = ()


@dataclass(frozen=True, slots=True)
class GateReason:
    """One reason the gate reached an outcome. Reasons compose; the worst outcome wins."""

    code: str
    outcome: GateOutcome
    detail: str


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    """The composed gate decision for one customer turn."""

    outcome: GateOutcome
    as_of: datetime
    tenant: str
    market: str
    intent_id: str = ""
    action_id: str = ""
    confidence: float = 0.0
    confidence_floor: float = 0.0
    reasons: tuple[GateReason, ...] = ()
    citations: tuple[Citation, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.outcome == GateOutcome.ALLOW

    @property
    def denied(self) -> bool:
        return self.outcome == GateOutcome.DENY


# --------------------------------------------------------------------------------------- #
# Actions and maker-checker
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """One parameter of an action, as the catalog declares it.

    ``binds_to_party`` says the value NAMES A RECORD somebody owns, so the caller must be shown
    to own it before the action runs. A pattern proves shape and nothing else: ``[0-9]{4}``
    cannot tell one customer's card from another's, and an allowed intent plus four well-formed
    digits was enough to read a stranger's balance.
    """

    name: str
    kind: str = "string"
    required: bool = True
    #: An anchored regular expression the value must match in full. Empty means no constraint.
    pattern: str = ""
    #: Does this value name a record a party owns? Required in the pack, for the reason
    #: ``consequential`` is required on actions: a silent default is how it gets forgotten.
    binds_to_party: bool = False


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """One executable action, as the catalog declares it.

    ``consequential`` is catalog METADATA, not a caller's opinion: an action the catalog marks
    consequential never auto-executes, whatever the gate said and whoever asked.
    """

    action_id: str
    title: str
    consequential: bool = True
    parameters: tuple[ParameterSpec, ...] = ()
    severity: Severity = Severity.MEDIUM


@dataclass(frozen=True, slots=True)
class ActionCall:
    """A validated request to execute one action on behalf of one contact.

    ``vertical`` travels with the call because the catalog is scoped by it: two lines of
    business may declare the same ``action_id`` and mean different things by it, so the
    executor must be told which catalog the caller was reading.
    """

    action_id: str
    contact_id: str
    tenant: str
    vertical: str
    #: The party the call is made on behalf of. Empty means unidentified, which owns nothing.
    party_ref: str = ""
    parameters: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """What happened to an action request. ``executed`` is the honest bit."""

    action_id: str
    executed: bool
    detail: str
    reference: str = ""
    requires_human_review: bool = False
    review_ref: str = ""


# --------------------------------------------------------------------------------------- #
# Handoff
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class HandoffPackage:
    """Everything the receiving human agent needs, and nothing the customer did not consent to.

    The transcript carried here is the REDACTED one. The package is schema-checked before it
    leaves (see ``handoff.validate_package``): an incomplete handoff is worse than none, because
    the customer has already been told a person is coming.
    """

    contact_id: str
    tenant: str
    market: str
    trigger: HandoffTrigger
    created_at: datetime
    summary: str
    turns: tuple[SpeakerTurn, ...] = ()
    procedure_state_id: str = ""
    carry_over_state_ids: tuple[str, ...] = ()
    gate_verdicts: tuple[PolicyVerdict, ...] = ()
    pending_action: ActionCall | None = None
    citations: tuple[Citation, ...] = ()


# --------------------------------------------------------------------------------------- #
# The two modes' results
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class AssistResult:
    """What the agent-assist whisper panel is given after one turn."""

    contact: ContactRef
    transcript: Transcript
    screen: ScreenResult
    progress: ProcedureProgress
    next_step: NextBestStep
    disclosures: DisclosureReport
    suggestion: SuggestedReply | None = None
    requires_human_review: bool = False
    review_ref: str = ""
    #: True when the guardrail screen was unavailable and the mode degraded to engine-only.
    deterministic_only: bool = False

    @property
    def mode(self) -> ContactMode:
        return ContactMode.AGENT_ASSIST


@dataclass(frozen=True, slots=True)
class SelfServiceResult:
    """What the customer-facing assistant produced for one turn."""

    contact: ContactRef
    transcript: Transcript
    screen: ScreenResult
    verdict: PolicyVerdict
    disclosures: DisclosureReport
    suggestion: SuggestedReply | None = None
    action: ActionOutcome | None = None
    handoff: HandoffPackage | None = None
    requires_human_review: bool = False
    review_ref: str = ""

    @property
    def mode(self) -> ContactMode:
        return ContactMode.SELF_SERVICE

    @property
    def contained(self) -> bool:
        """Contained means resolved in self-service: allowed, answered and not handed off."""
        return self.verdict.allowed and self.handoff is None


#: What rule R8 may be asked to route. Both modes escalate, and the review payload builder in
#: ``adapters/_review_payload.py`` reads only the members they have in common (the contact, the
#: mode, the severity band and the citations), so a third mode would extend this union without
#: touching the router.
ReviewableResult = AssistResult | SelfServiceResult
