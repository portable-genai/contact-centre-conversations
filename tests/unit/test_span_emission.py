"""Both mode entry points open ONE span, and neither span carries content.

A trace backend is not the WORM audit trail. It has no redaction stage, no retention policy
written against a regulator's requirement, and a far wider read audience than the audit store. So
the value of tracing the two contact paths depends entirely on the span carrying STRUCTURAL
attributes only: which action, whose, which tenant, which market, how long. A customer utterance,
an agent's turn text, a matched intent's wording or a planted identifier reaching a span has left
the boundary ``TurnGuard`` exists to hold, and it has left it silently.

The content cases here drive the turn that carries the planted NRIC, so the check runs against
input that would actually leak if any attribute were content-shaped. They also reject the
REDACTED turn text, which no masking would catch: it is still the customer's words.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from contact_centre_conversations.config import (
    build_container,
)
from contact_centre_conversations.domain.assist_service import (
    AgentAssistService,
)
from contact_centre_conversations.domain.models import (
    TurnSubmission,
)
from contact_centre_conversations.domain.self_service import (
    SelfServiceService,
)
from contact_centre_conversations.services import (
    build_services,
)

from tests.conftest import local_settings
from tests.fixtures import sample_cases

_AS_OF = datetime(2026, 8, 8, 9, 0, tzinfo=UTC)

_ASSIST_SPAN = "contact_centre_conversations.agent_assist.turn"
_SELF_SERVICE_SPAN = "contact_centre_conversations.self_service.turn"

#: The whole attribute vocabulary either span may use. Written out here rather than derived from
#: the domain, so a new attribute has to be added in TWO places and the second one is a review.
_ALLOWED_ATTRIBUTES = {"action", "actor", "tenant", "market"}

#: The planted-identifier turn as a CUSTOMER utterance, so the self-service path is checked
#: against leaking input too. Same fixture text the redaction proofs use; nothing new is invented.
_PII_CUSTOMER_TURN = TurnSubmission(
    contact=sample_cases.CUSTOMER_CONTACT,
    index=0,
    speaker_id="customer",
    role=sample_cases.ChannelRole.CUSTOMER,
    text=sample_cases.PII_TURN.text,
)


class _RecordingTracer:
    """Captures every span name and attribute so the test can inspect what was emitted."""

    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, str]]] = []

    @contextmanager
    def span(self, name: str, **attributes: str) -> Iterator[None]:
        self.spans.append((name, dict(attributes)))
        yield

    def record_token_usage(self, usage: object, model: str) -> None:
        return None


Runner = Callable[[TurnSubmission], _RecordingTracer]


def _observe(submission: TurnSubmission) -> _RecordingTracer:
    """Run one agent-assist turn against a recording tracer and return what it saw."""
    container = build_container(local_settings())
    tracer = _RecordingTracer()
    service = AgentAssistService(
        kernel=build_services(container).kernel,
        packs=container.settings.packs,
        review_router=container.review_router,
        tracer=tracer,
    )
    service.observe(submission, actor=sample_cases.ACTOR, as_of=_AS_OF)
    return tracer


def _handle(submission: TurnSubmission) -> _RecordingTracer:
    """Run one self-service turn against a recording tracer and return what it saw."""
    container = build_container(local_settings())
    tracer = _RecordingTracer()
    service = SelfServiceService(
        kernel=build_services(container).kernel,
        packs=container.settings.packs,
        tools=container.tool_catalog,
        party_records=container.party_records,
        review_router=container.review_router,
        tracer=tracer,
    )
    service.handle(submission, actor=sample_cases.ACTOR, as_of=_AS_OF)
    return tracer


#: Every (runner, submission, expected span name) the assertions below sweep. Both modes, and
#: within each mode both a compliant turn and one that is refused or escalates, because a span
#: vocabulary that widens only on the unhappy path is the one nobody notices.
_CASES = [
    pytest.param(_observe, sample_cases.OPENING_TURN, _ASSIST_SPAN, id="assist-clean"),
    pytest.param(_observe, sample_cases.PII_TURN, _ASSIST_SPAN, id="assist-pii"),
    pytest.param(_handle, sample_cases.IN_SCOPE_TURN, _SELF_SERVICE_SPAN, id="self-in-scope"),
    pytest.param(_handle, sample_cases.OUT_OF_SCOPE_TURN, _SELF_SERVICE_SPAN, id="self-refused"),
    pytest.param(_handle, sample_cases.INJECTION_TURN, _SELF_SERVICE_SPAN, id="self-injection"),
]


@pytest.mark.parametrize(("run", "submission", "expected"), _CASES)
def test_each_entry_point_opens_exactly_one_named_span(
    run: Runner, submission: TurnSubmission, expected: str
) -> None:
    """One turn is one unit of work, so it is one span, whatever the path through it."""
    assert [name for name, _ in run(submission).spans] == [expected]


@pytest.mark.parametrize(("run", "submission", "expected"), _CASES)
def test_the_span_carries_the_structural_attributes_an_operator_needs(
    run: Runner, submission: TurnSubmission, expected: str
) -> None:
    """Enough to answer "whose turns are slow, in which tenant and market", and nothing more."""
    _, attributes = run(submission).spans[0]
    assert attributes["action"] == expected.removeprefix("contact_centre_conversations.")
    assert attributes["actor"] == sample_cases.ACTOR
    assert attributes["tenant"] == sample_cases.TENANT
    assert attributes["market"] == sample_cases.MARKET


@pytest.mark.parametrize(("run", "submission", "expected"), _CASES)
def test_the_attribute_set_is_a_fixed_allowlist_whatever_the_outcome(
    run: Runner, submission: TurnSubmission, expected: str
) -> None:
    """A refused turn must not start attaching its evidence to the span to explain itself."""
    for _, attributes in run(submission).spans:
        assert set(attributes) == _ALLOWED_ATTRIBUTES


@pytest.mark.parametrize(
    ("run", "submission"),
    [
        pytest.param(_observe, sample_cases.PII_TURN, id="assist"),
        pytest.param(_handle, _PII_CUSTOMER_TURN, id="self-service"),
    ],
)
def test_no_span_attribute_carries_turn_text_or_a_planted_identifier(
    run: Runner, submission: TurnSubmission
) -> None:
    """The turn used here has an NRIC planted in it, so a leak would show as a literal."""
    tracer = run(submission)
    emitted = " ".join(value for _, attributes in tracer.spans for value in attributes.values())
    assert sample_cases.PLANTED_NRIC not in emitted
    assert sample_cases.PLANTED_NRIC.lower() not in emitted.lower()
    assert submission.text not in emitted
    # Word by word as well as whole: the MASKED turn text is still the customer's words, and a
    # span attribute carrying it would pass every check that only looks for the raw identifier.
    tokens = set(emitted.split())
    assert not tokens & set(submission.text.split()), (
        "a span attribute is quoting the turn: that is content, not structure"
    )
