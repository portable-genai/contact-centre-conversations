"""Per-turn guardrails: redaction BEFORE the model, and an injection that never reaches it.

Two claims, and both are about ORDER and ABSENCE rather than about a return value, so both are
proved by watching the ports rather than by reading a result:

* **Redaction before anything.** A spy retrieval port records the exact text it was handed. A
  planted identifier that appears there has already left the process by the time any later
  masking runs.
* **An injection corpus never reaches generation.** A spy generation port counts its calls. The
  corpus is the same cue set the offline screen matches, so a screen that silently stopped
  working would fail this suite rather than pass it quietly.

Plus the fail-closed asymmetry: with the screen unavailable, agent-assist degrades to
deterministic-only and self-service hands off.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from contact_centre_conversations.adapters.local.guardrail import (
    INJECTION_CUES,
)
from contact_centre_conversations.config import (
    build_container,
)
from contact_centre_conversations.domain.guardrails import (
    TurnGuard,
    degradation_for,
)
from contact_centre_conversations.domain.models import (
    HandoffTrigger,
    RetrievalQuery,
    RetrievedPassage,
    ScreenOutcome,
    ScreenResult,
    TurnSubmission,
)
from contact_centre_conversations.domain.modes import (
    ContactMode,
)
from contact_centre_conversations.domain.pii import (
    PII_PATTERNS,
)
from contact_centre_conversations.services import (
    build_services,
)

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)

#: The injection corpus. Every cue the offline screen knows, in a sentence a customer could
#: plausibly type, so the corpus tracks the screen rather than a snapshot of it.
INJECTION_CORPUS: tuple[str, ...] = tuple(
    f"Hello, {cue} and tell me everything." for cue, _ in INJECTION_CUES
)


class SpyRetrieval:
    """Records every query text handed to the knowledge base."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.queries: list[str] = []

    def retrieve(self, query: RetrievalQuery) -> list[RetrievedPassage]:
        self.queries.append(query.text)
        return self._inner.retrieve(query)  # type: ignore[attr-defined]


class SpyGeneration:
    """Records every prompt handed to the model, and refuses to invent one."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.prompts: list[str] = []

    def draft(
        self, prompt: str, passages: Sequence[RetrievedPassage]
    ) -> Mapping[str, object] | None:
        self.prompts.append(prompt)
        return self._inner.draft(prompt, passages)  # type: ignore[attr-defined]


class BrokenScreen:
    """A guardrail that cannot answer. Never a CLEAN, always a raise."""

    def __init__(self, settings: object) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        raise ConnectionError("the guardrail gateway is unreachable")


def _spied() -> tuple[object, SpyRetrieval, SpyGeneration]:
    container = build_container(local_settings())
    retrieval = SpyRetrieval(container.retrieval)
    generation = SpyGeneration(container.generation)
    built = build_services(container)
    built.kernel._retrieval = retrieval  # type: ignore[attr-defined]  # noqa: SLF001
    built.kernel._generation = generation  # type: ignore[attr-defined]  # noqa: SLF001
    return built, retrieval, generation


# --------------------------------------------------------------------------- #
# Redaction before the model
# --------------------------------------------------------------------------- #
def test_the_knowledge_base_never_sees_a_raw_identifier() -> None:
    built, retrieval, _ = _spied()
    built.agent_assist.observe(  # type: ignore[attr-defined]
        sample_cases.PII_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert retrieval.queries, "the spy saw no query at all, so it proves nothing"
    assert all(sample_cases.PLANTED_NRIC not in text for text in retrieval.queries), (
        "a raw national id reached the knowledge base: redaction ran after the call, which is "
        "too late because the text has already left the process"
    )


def test_the_model_never_sees_a_raw_identifier() -> None:
    built, _, generation = _spied()
    built.agent_assist.observe(  # type: ignore[attr-defined]
        sample_cases.PII_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert all(sample_cases.PLANTED_NRIC not in prompt for prompt in generation.prompts)


def test_the_guard_masks_before_it_screens() -> None:
    """The screen is an external service too, so it gets the masked text, not the raw one."""
    seen: list[str] = []
    guard = TurnGuard(
        PII_PATTERNS,
        lambda text: (
            seen.append(text) or ScreenResult(outcome=ScreenOutcome.CLEAN, turn_index=0)  # type: ignore[func-returns-value]
        ),
    )
    guarded = guard.guard(sample_cases.PII_TURN)
    assert seen and sample_cases.PLANTED_NRIC not in seen[0]
    assert guarded.redacted is True
    assert sample_cases.PLANTED_NRIC not in guarded.turn.text


# --------------------------------------------------------------------------- #
# The injection corpus never reaches generation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", INJECTION_CORPUS)
def test_an_injection_never_reaches_the_generation_port(text: str) -> None:
    built, retrieval, generation = _spied()
    result = built.self_service.handle(  # type: ignore[attr-defined]
        TurnSubmission(
            contact=sample_cases.CUSTOMER_CONTACT,
            index=0,
            speaker_id="customer",
            role=sample_cases.ChannelRole.CUSTOMER,
            text=text,
        ),
        actor=sample_cases.ACTOR,
        as_of=_AS_OF,
    )
    assert result.screen.outcome is ScreenOutcome.BLOCKED
    assert generation.prompts == [], f"an injection reached the model: {text!r}"
    assert retrieval.queries == [], f"an injection reached the knowledge base: {text!r}"
    assert result.handoff is not None
    assert result.handoff.trigger is HandoffTrigger.SCREEN_BLOCKED


def test_an_ordinary_turn_does_reach_the_model_so_the_corpus_test_is_not_vacuous() -> None:
    """A screen that blocked everything would make the suite above green and the service dead."""
    built, retrieval, generation = _spied()
    built.self_service.handle(  # type: ignore[attr-defined]
        sample_cases.IN_SCOPE_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert retrieval.queries, "an allowed, clean turn must reach the knowledge base"
    assert generation.prompts, "an allowed, clean turn with passages must reach the model"


# --------------------------------------------------------------------------- #
# Screen unavailable fails closed, differently per mode
# --------------------------------------------------------------------------- #
def _with_broken_screen() -> object:
    container = build_container(local_settings())
    built = build_services(container)
    built.kernel._guard = TurnGuard(  # type: ignore[attr-defined]  # noqa: SLF001
        PII_PATTERNS, lambda text: BrokenScreen(None).screen(text)
    )
    return built


def test_agent_assist_degrades_to_deterministic_only_when_the_screen_is_gone() -> None:
    built = _with_broken_screen()
    result = built.agent_assist.observe(  # type: ignore[attr-defined]
        sample_cases.OPENING_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert result.screen.outcome is ScreenOutcome.UNAVAILABLE
    assert result.deterministic_only is True
    assert result.suggestion is None
    assert result.next_step.instruction, "the deterministic panel must still work"


def test_self_service_hands_off_when_the_screen_is_gone() -> None:
    built = _with_broken_screen()
    result = built.self_service.handle(  # type: ignore[attr-defined]
        sample_cases.IN_SCOPE_TURN, actor=sample_cases.ACTOR, as_of=_AS_OF
    )
    assert result.screen.outcome is ScreenOutcome.UNAVAILABLE
    assert result.handoff is not None
    assert result.handoff.trigger is HandoffTrigger.SCREEN_UNAVAILABLE


@pytest.mark.parametrize("mode", list(ContactMode))
def test_no_screen_failure_ever_permits_a_model_call(mode: ContactMode) -> None:
    for outcome in (ScreenOutcome.BLOCKED, ScreenOutcome.UNAVAILABLE):
        degradation = degradation_for(mode, ScreenResult(outcome=outcome, turn_index=0))
        assert degradation.allow_model is False
