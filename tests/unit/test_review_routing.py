"""Rule R8: an escalated result is ROUTED to human-review-console, not left in a per-repo boolean.

This is the standing gate for the failure the rule exists to prevent. A repo can set
``requires_human_review = True``, pass every other test, and still auto-execute in practice
because nothing ever reads the flag. So the assertions here are about the ROUTING, not the flag:
an escalation produces an outbound review, a compliant turn produces none, the payload leaves
redacted, the review is TAGGED with the mode that produced it, and the on-prem placeholder
refuses rather than swallowing the escalation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contact_centre_conversations.adapters.gcp.review_router import (
    CloudReviewRouter,
)
from contact_centre_conversations.adapters.local.review_router import (
    LocalReviewRouter,
)
from contact_centre_conversations.adapters.onprem.review_router import (
    OnPremReviewRouter,
)
from contact_centre_conversations.api.app import (
    app,
)
from contact_centre_conversations.domain.kernel import (
    Severity,
)

from tests.conftest import local_settings
from tests.contract.canonical import CANONICAL_RESULT, CANONICAL_SELF_SERVICE_RESULT
from tests.fixtures import sample_cases

_MAKER = sample_cases.ACTOR


def test_an_escalated_result_produces_an_outbound_review() -> None:
    router = LocalReviewRouter(local_settings())
    ref = router.route(CANONICAL_RESULT, maker=_MAKER)
    assert ref, "routing must return a reference, so the caller can record where it went"
    pending = router.outbox.pending()
    assert len(pending) == 1
    review = pending[0].review
    assert review.maker == _MAKER
    assert review.tenant == sample_cases.TENANT
    assert review.source_key, "a durable outbox needs an idempotency key"


def test_the_review_is_tagged_with_the_mode_that_produced_it() -> None:
    """Each mode promotes on its OWN evidence, so its reviews must be separable in the console."""
    router = LocalReviewRouter(local_settings())
    router.route(CANONICAL_RESULT, maker=_MAKER)
    router.route(CANONICAL_SELF_SERVICE_RESULT, maker=_MAKER)
    reviews = [entry.review for entry in router.outbox.pending()]
    assert [review.action for review in reviews] == [
        "contact_centre_conversations:agent_assist",
        "contact_centre_conversations:self_service",
    ]
    assert len({review.sod_group for review in reviews}) == 2, (
        "the two modes share a segregation-of-duty group, so one mode's checkers could sign "
        "off the other's escalations"
    )
    assert len({review.source_key for review in reviews}) == 2


def test_the_payload_is_redacted_before_it_leaves_the_process() -> None:
    """human-review-console is a shared sink; a raw identifier must never reach the wire."""
    from contact_centre_conversations.adapters._review_payload import result_to_review

    review = result_to_review(CANONICAL_RESULT, maker=_MAKER, tenant=sample_cases.TENANT)
    wire = repr(review.to_payload())
    assert sample_cases.PLANTED_NRIC not in wire
    assert review.severity in {s.value for s in Severity}


def test_the_managed_router_refuses_when_no_console_is_configured() -> None:
    """An escalation with nowhere to go must fail loudly, not return as if it were reviewed."""
    router = CloudReviewRouter(local_settings(profile="gcp", review_url=""))
    with pytest.raises(RuntimeError, match="R8"):
        router.route(CANONICAL_RESULT, maker=_MAKER)


def test_the_onprem_placeholder_refuses_rather_than_dropping_the_escalation() -> None:
    router = OnPremReviewRouter(local_settings(profile="onprem"))
    with pytest.raises(NotImplementedError, match="R8"):
        router.route(CANONICAL_RESULT, maker=_MAKER)


def test_the_api_routes_the_escalation_in_the_same_request() -> None:
    """The serving path, not just the adapter: an escalation must not depend on a later job."""
    client = TestClient(app, client=("127.0.0.1", 50000))
    body: dict[str, object] = {}
    script = (
        ("Thanks for calling, how can I help you today?", 0, 5_000, False),
        (
            "Can you confirm your date of birth and the last four of your card?",
            9_500,
            18_000,
            False,
        ),
        (
            "I have blocked the card now and you will receive a replacement card.",
            55_000,
            64_000,
            True,
        ),
    )
    for index, (text, start_ms, end_ms, ends) in enumerate(script):
        body = client.post(
            "/v1/agent-assist/turn",
            json={
                "contact_id": sample_cases.MISSED_DISCLOSURE_CONTACT_ID,
                "market": sample_cases.MARKET,
                "locale": sample_cases.LOCALE,
                "vertical": sample_cases.VERTICAL,
                "text": text,
                "index": index,
                "speaker_id": "agent-1",
                "role": "agent",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "ends_contact": ends,
            },
            headers={"X-Dev-Persona": "auditor"},
        ).json()
    assert body["missed_disclosure_ids"] == ["recording_notice"]
    assert body["requires_human_review"] is True
    assert body["review_ref"], "an escalation with no routing reference went nowhere"


def test_a_compliant_contact_does_not_manufacture_a_review() -> None:
    client = TestClient(app, client=("127.0.0.1", 50000))
    body = client.post(
        "/v1/agent-assist/turn",
        json={
            "contact_id": sample_cases.CLEAN_CONTACT_ID,
            "market": sample_cases.MARKET,
            "locale": sample_cases.LOCALE,
            "vertical": sample_cases.VERTICAL,
            "text": "Thank you for calling. This call is being recorded for quality.",
            "speaker_id": "agent-1",
            "role": "agent",
            "start_ms": 0,
            "end_ms": 6000,
        },
        headers={"X-Dev-Persona": "auditor"},
    ).json()
    assert body["requires_human_review"] is False
    assert body["review_ref"] == "", "a non-escalation must not manufacture a review"
