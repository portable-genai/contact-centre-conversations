"""Tool functions an agent runtime calls: thin, side-effect-honest wrappers on the services.

Design rules, in the order they matter:

* **No business logic here.** The mode services decide HOW; the model only decides WHICH tool to
  call. A rule that lives in a tool wrapper is a rule the CLI and the API do not have.
* **The MODE GATE applies on this path too.** Each mode tool calls ``services.require_mode``
  before it does anything, so an agent runtime cannot reach a mode the deployment disabled. Both
  modes are off until a deployment enables them, so a freshly bound agent can do neither.
* **Rule R8 applies on this path too.** The service routes an escalation in the same call that
  produced it, and the reference is returned so a caller can tell a routed escalation from a
  flag that stopped here.
* **Import-safe without a runtime.** ``google.adk`` is imported lazily inside
  :func:`build_function_tools`, so these callables are importable, testable and runnable with no
  ADK and no cloud SDK installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hex_service_kit.serialization import to_jsonable
from pii_kit import redact
from speech_lexicon_kit import ChannelRole

from .. import services
from ..config import Container, Settings, build_container
from ..domain.kernel import utcnow
from ..domain.models import ContactRef, TurnSubmission
from ..domain.modes import ContactMode
from ..domain.pii import PII_PATTERNS

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from google.adk.tools import FunctionTool

#: The identity a tool call is attributed to when the runtime propagates none. It names the
#: SERVICE, not a person, so an unattributed action is never mistaken for a human's.
DEFAULT_ACTOR = "contact-centre-conversations-agent"


def _container(settings: Settings | None) -> Container:
    return build_container(settings)


def _redacted(node: Any) -> Any:
    """Mask personal data in every string of a tool result, however deeply it is nested.

    A tool result is not an API response. The API returns a panel to the authenticated agent who
    is already on the contact; a TOOL result goes into a model's context, and P-04 says minimise
    what reaches a model. Walking the whole structure rather than three named fields means a
    field added in a later slice cannot arrive unredacted because nobody remembered it.
    """
    if isinstance(node, str):
        return redact(node, PII_PATTERNS)
    if isinstance(node, dict):
        return {key: _redacted(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_redacted(value) for value in node]
    return node


def _submission(
    *,
    contact_id: str,
    tenant: str,
    market: str,
    locale: str,
    text: str,
    index: int,
    mode: ContactMode,
    role: ChannelRole,
    start_ms: int | None = None,
    end_ms: int | None = None,
    ends_contact: bool = False,
) -> TurnSubmission:
    return TurnSubmission(
        contact=ContactRef(
            contact_id=contact_id, tenant=tenant, market=market, locale=locale, mode=mode
        ),
        index=index,
        speaker_id=role.value,
        role=role,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        ends_contact=ends_contact,
    )


def whisper_panel(
    contact_id: str,
    text: str,
    tenant: str = "demo-bank",
    market: str = "SG",
    locale: str = "en-SG",
    index: int = 0,
    start_ms: int | None = None,
    end_ms: int | None = None,
    ends_contact: bool = False,
    actor: str = DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Produce the agent-assist whisper panel for one turn of a live contact.

    Advances the deterministic procedure engine from what was actually said, evaluates the
    market's disclosure windows, and attaches a KB-grounded suggestion only when retrieval
    produced passages to ground it in. A missed disclosure window routes to human review in this
    same call (rule R8).

    Args:
      contact_id: The contact this turn belongs to.
      text: What was said. Redacted and screened before anything else sees it.
      tenant: Tenant partition. A contact belonging to another tenant is refused.
      market: Which market's procedure and disclosure packs apply.
      locale: The transcript locale, which selects phrase normalisation.
      index: The turn's index within the contact.
      start_ms: Where the turn starts, in contact milliseconds. A disclosure window cannot be
        judged without offsets, so a turn with none leaves timed disclosures unverifiable
        rather than silently satisfied.
      end_ms: Where the turn ends, in contact milliseconds.
      ends_contact: True on the last turn, which closes every open disclosure window.
      actor: The verified identity this call is attributed to.

    Returns:
      A JSON-safe panel with every string masked for personal data, plus ``review_ref``: where
      an escalation WENT. Empty only when nothing escalated.
    """
    container = _container(settings)
    services.require_mode(container, ContactMode.AGENT_ASSIST)
    built = services.build_services(container)
    result = built.agent_assist.observe(
        _submission(
            contact_id=contact_id,
            tenant=tenant,
            market=market,
            locale=locale,
            text=text,
            index=index,
            mode=ContactMode.AGENT_ASSIST,
            role=ChannelRole.AGENT,
            start_ms=start_ms,
            end_ms=end_ms,
            ends_contact=ends_contact,
        ),
        actor=actor,
        as_of=utcnow(),
    )
    return _panel_payload(
        {
            "state_id": result.progress.state_id,
            "completed_state_ids": list(result.progress.completed_state_ids),
            "next_step": to_jsonable(result.next_step),
            "due_disclosure_ids": [s.disclosure_id for s in result.disclosures.due],
            "missed_disclosure_ids": [s.disclosure_id for s in result.disclosures.missed],
            "suggestion": to_jsonable(result.suggestion),
            "screen": result.screen.outcome.value,
            "deterministic_only": result.deterministic_only,
            "requires_human_review": result.requires_human_review,
        },
        review_ref=result.review_ref,
    )


def self_service_reply(
    contact_id: str,
    text: str,
    tenant: str = "demo-bank",
    market: str = "SG",
    locale: str = "en-SG",
    index: int = 0,
    requested_action: str = "",
    actor: str = DEFAULT_ACTOR,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Answer one customer turn in self-service, or refuse it and hand off to a person.

    The gate is a fail-closed allowlist of intents and, separately, of actions, per tenant and
    market. Anything unmatched, ambiguous or below the configured confidence floor denies and
    triggers a handoff. An action the catalog marks consequential never auto-executes.

    Args:
      contact_id: The contact this turn belongs to.
      text: What the customer said. Redacted and screened before anything else sees it.
      tenant: Tenant partition. A contact belonging to another tenant is refused.
      market: Which market's allowlist, procedure and disclosure packs apply.
      locale: The transcript locale, which selects phrase normalisation.
      index: The turn's index within the contact.
      requested_action: The action this turn is asking for, if any.
      actor: The verified identity this call is attributed to.

    Returns:
      A JSON-safe result with every string masked for personal data, including the gate verdict
      and its reasons, the handoff trigger where one fired, and ``review_ref``.
    """
    container = _container(settings)
    services.require_mode(container, ContactMode.SELF_SERVICE)
    built = services.build_services(container)
    result = built.self_service.handle(
        _submission(
            contact_id=contact_id,
            tenant=tenant,
            market=market,
            locale=locale,
            text=text,
            index=index,
            mode=ContactMode.SELF_SERVICE,
            role=ChannelRole.CUSTOMER,
        ),
        actor=actor,
        as_of=utcnow(),
        requested_action=requested_action,
    )
    return _panel_payload(
        {
            "verdict": to_jsonable(result.verdict),
            "suggestion": to_jsonable(result.suggestion),
            "action": to_jsonable(result.action),
            "handoff_trigger": result.handoff.trigger.value if result.handoff else "",
            "screen": result.screen.outcome.value,
            "contained": result.contained,
            "requires_human_review": result.requires_human_review,
        },
        review_ref=result.review_ref,
    )


def verify_audit_trail(settings: Settings | None = None) -> dict[str, Any]:
    """Verify the audit trail's hash chain and its external head anchor.

    Returns:
      A dict with ``ok``, the record counts and a ``detail`` string. ``ok`` is false for an
      edited, deleted or reordered record, and, when an external anchor is configured, for a
      truncated tail as well. Without an anchor a truncation cannot be detected, and the detail
      says so rather than implying a stronger guarantee than the store provides.
    """
    resolved = settings or Settings.load()
    audit = _container(resolved).audit
    verify = getattr(audit, "verify", None)
    if verify is None:
        raise NotImplementedError(
            f"the {resolved.profile} audit adapter does not expose chain verification; a "
            "managed WORM sink is verified by its own retention policy, not from here"
        )
    report = verify()
    return {
        "ok": report.ok,
        "entries": report.entries,
        "chained": report.chained,
        "legacy": report.legacy,
        "first_bad_seq": report.first_bad_seq,
        "detail": report.detail,
        "anchored": bool(resolved.audit_anchor_path),
    }


def _panel_payload(payload: dict[str, Any], *, review_ref: str) -> dict[str, Any]:
    redacted = _redacted(payload)
    if not isinstance(redacted, dict):  # pragma: no cover - a dict redacts to a dict
        raise TypeError("a tool result must serialise to a JSON object")
    # Attached AFTER the redaction pass: it is a routing reference, not narrative text, and
    # masking an identifier inside it would break the caller's ability to look the review up.
    redacted["review_ref"] = review_ref
    return redacted


#: The tool table. The agent card advertises exactly these, by function name.
TOOL_FUNCTIONS = (whisper_panel, self_service_reply, verify_audit_trail)


def build_function_tools() -> list[FunctionTool]:
    """Wrap each callable as a runtime FunctionTool (the only ADK-dependent code path).

    The import is deliberately here rather than at module scope: without it this module, the
    card and every tool would need an agent runtime installed to be imported at all, and the
    offline gate installs none.
    """
    from google.adk.tools import FunctionTool

    return [FunctionTool(func=function) for function in TOOL_FUNCTIONS]
