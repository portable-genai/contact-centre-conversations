"""Shared conversion from an escalated result to an ``review-kit`` Review payload.

Lives in the adapter layer, not the pure domain, because it depends on the kit. Everything that
crosses the wire is redacted BEFORE it leaves the process (the same redact-before-anything rule
the audit write obeys), using the shared ``pii-kit``, so no raw identifier reaches Hrz7; Hrz7
redacts again before its own audit write (defence in depth). ``maker`` and ``tenant`` are
asserted here and trusted by Hrz7 because the caller is an authenticated S2S service.

**The mode travels with the review.** Each of E1's two modes is its own Hrz4 gated release, so a
reviewer needs to know whether they are looking at a whisper-panel escalation from an
internal-facing copilot or a refusal from a customer-facing assistant. It is carried in the
``action`` and in the ``sod_group`` rather than only in the summary, so the console can route
and separate on it without parsing prose.
"""

from __future__ import annotations

import re

from pii_kit import NATIONAL_ID_PATTERNS, UNIVERSAL_PATTERNS, national_patterns_for
from pii_kit import redact as pii_redact
from review_kit import Citation as KitCitation
from review_kit import Review

from ..domain.kernel import Citation, Severity
from ..domain.models import AssistResult, ReviewableResult, SelfServiceResult

#: Cap the citations carried on the wire: enough for a reviewer to trace the decision without
#: copying the whole evidence set into the console.
_MAX_CITATIONS = 8

#: The console is a SHARED sink: a contact in one market may still quote another market's
#: national id, so the payload is scrubbed against every jurisdiction's rows plus the universal
#: email/phone rows, whatever this deployment's own ``domain.pii.JURISDICTIONS`` selects.
_ALL_PATTERNS = (
    *national_patterns_for(tuple(NATIONAL_ID_PATTERNS.keys())),
    *UNIVERSAL_PATTERNS,
)

#: Bands that demand dual control (two approvals) rather than a single checker.
_DUAL_CONTROL = (Severity.CRITICAL,)

_PREFIX = "contact_centre_conversations"


def _redact(text: str) -> str:
    """Mask every jurisdiction's identifiers plus email/phone, and normalise whitespace."""
    return re.sub(r"\s+", " ", pii_redact(text, _ALL_PATTERNS)).strip()


def _severity(result: ReviewableResult) -> Severity:
    """The band the review carries: the worst missed disclosure, or the mode's own floor."""
    missed = result.disclosures.missed
    order = (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL)
    if missed:
        return max((status.severity for status in missed), key=order.index)
    if isinstance(result, SelfServiceResult) and result.action is not None:
        return Severity.HIGH if result.action.requires_human_review else Severity.MEDIUM
    return Severity.MEDIUM


def _summary(result: ReviewableResult) -> str:
    if isinstance(result, AssistResult):
        missed = ", ".join(status.disclosure_id for status in result.disclosures.missed)
        return (
            f"agent-assist at state {result.progress.state_id}; "
            f"missed disclosures: {missed or 'none'}; "
            f"screen {result.screen.outcome.value}"
        )
    action = result.action.action_id if result.action else "none"
    return (
        f"self-service gate {result.verdict.outcome.value} on intent "
        f"{result.verdict.intent_id or 'none'}; pending action {action}; "
        f"handoff {result.handoff.trigger.value if result.handoff else 'none'}"
    )


def _case_ref(result: ReviewableResult) -> str:
    if isinstance(result, AssistResult):
        return f"{result.contact.contact_id}:{result.progress.state_id}"
    return f"{result.contact.contact_id}:{result.verdict.outcome.value}"


def _kit_citations(result: ReviewableResult) -> tuple[KitCitation, ...]:
    citations = (
        [*result.next_step.citations, *_disclosure_citations(result)]
        if isinstance(result, AssistResult)
        else [*result.verdict.citations, *_disclosure_citations(result)]
    )
    seen: set[str] = set()
    out: list[KitCitation] = []
    for citation in citations:
        if citation.source_id in seen:
            continue
        seen.add(citation.source_id)
        out.append(
            KitCitation(
                source_id=citation.source_id,
                title=citation.title,
                snippet=_redact(citation.snippet),
            )
        )
        if len(out) >= _MAX_CITATIONS:
            break
    return tuple(out)


def _disclosure_citations(result: ReviewableResult) -> list[Citation]:
    return [citation for status in result.disclosures.missed for citation in status.citations]


def result_to_review(result: ReviewableResult, *, maker: str, tenant: str = "") -> Review:
    """Build the review a producer submits to Hrz7 when a result escalates (rule R8)."""
    mode = result.mode.value
    severity = _severity(result)
    contact = result.contact
    return Review(
        action=f"{_PREFIX}:{mode}",
        subject=_redact(f"{contact.contact_id} ({contact.market})"),
        maker=maker,
        tenant=tenant or contact.tenant,
        summary=_redact(_summary(result)),
        severity=severity.value,
        required_approvals=2 if severity in _DUAL_CONTROL else 1,
        # Separated per mode: the two modes promote on their own evidence, so their reviews must
        # be separable in the console as well as in the eval.
        sod_group=f"{_PREFIX}-{mode}-maker-checker",
        case_ref=_case_ref(result),
        # Producer-owned, tenant-scoped key so a retried delivery is idempotent at the console.
        source_key=f"E1:{mode}:{_case_ref(result)}",
        citations=_kit_citations(result),
    )
