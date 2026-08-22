"""The agent surface is real, import-safe and cannot drift from the card it publishes.

Three failures this suite exists to prevent, all of which a scaffolded surface invites:

1. **A card that lies.** A skill advertised with no tool behind it, or a tool nobody can
   discover. The set equality below is the standing gate.
2. **A third place an escalation stops.** The API and the CLI both route an escalated result to
   human review. If the agent path only returned the flag, rule R8 would hold on two surfaces
   out of three, which is the same as not holding.
3. **A surface that quietly needs a runtime.** The tools must import and run with no ADK and no
   cloud SDK, or the offline gate stops being offline the day somebody calls one.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contact_centre_conversations.agent import (
    SKILLS,
    TOOL_FUNCTIONS,
    agent_card_document,
    build_agent_card,
    self_service_reply,
    verify_audit_trail,
    whisper_panel,
)
from contact_centre_conversations.config import (
    Settings,
)

from tests.conftest import local_settings
from tests.fixtures import sample_cases


def test_the_card_advertises_exactly_the_tools_the_runtime_binds() -> None:
    assert {skill.id for skill in SKILLS} == {fn.__name__ for fn in TOOL_FUNCTIONS}


def test_every_skill_carries_a_usable_description() -> None:
    """A runtime routes on the description; an empty one makes the skill unreachable."""
    for skill in SKILLS:
        assert skill.name.strip()
        assert len(skill.description.strip()) > 40, f"{skill.id} has no usable description"


def test_the_card_document_is_json_safe_and_names_the_region() -> None:
    document = agent_card_document(local_settings())
    assert document["name"] == "contact-centre-conversations"
    assert "asia-southeast1" in str(document["url"])
    assert [skill["id"] for skill in document["skills"]] == [s.id for s in SKILLS]


def test_the_api_serves_the_card_for_discovery(api_client: TestClient) -> None:
    resp = api_client.get("/.well-known/agent-card.json")
    assert resp.status_code == 200
    assert resp.json()["skills"], "a card with no skills is not a discovery document"


def test_the_agent_path_routes_an_escalation_rather_than_only_flagging_it() -> None:
    """A whole contact whose recording notice is never given: the last turn closes the window."""
    settings = local_settings()
    result: dict[str, object] = {}
    script = (
        ("Thanks for calling, how can I help you today?", 0, 5_000, False),
        (
            "Can you confirm your date of birth and the last four digits of your card?",
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
        result = whisper_panel(
            sample_cases.MISSED_DISCLOSURE_CONTACT_ID,
            text,
            tenant=sample_cases.TENANT,
            index=index,
            start_ms=start_ms,
            end_ms=end_ms,
            ends_contact=ends,
            actor=sample_cases.ACTOR,
            settings=settings,
        )
    assert result["requires_human_review"] is True
    assert result["review_ref"], "the agent surface flagged an escalation it never routed"


def test_the_agent_path_does_not_manufacture_a_review_for_a_compliant_turn() -> None:
    result = whisper_panel(
        sample_cases.CLEAN_CONTACT_ID,
        "Thank you for calling. This call is being recorded for quality.",
        tenant=sample_cases.TENANT,
        settings=local_settings(),
    )
    assert result["requires_human_review"] is False
    assert result["review_ref"] == ""


def test_the_self_service_tool_denies_an_out_of_scope_ask_and_hands_off() -> None:
    result = self_service_reply(
        sample_cases.SELF_SERVICE_CONTACT_ID,
        "Please refinance my mortgage and tell me which fund to buy.",
        tenant=sample_cases.TENANT,
        settings=local_settings(),
    )
    assert result["verdict"]["outcome"] == "deny"
    assert result["handoff_trigger"] == "gate_denial"


def test_the_tool_output_is_masked_before_it_can_reach_a_model() -> None:
    """P-04 at the agent boundary: a tool result becomes model context, so it is minimised.

    This is deliberately STRICTER than the API response, which returns a panel to the agent who
    is already on the contact. The difference is the destination, not the data.
    """
    result = whisper_panel(
        sample_cases.CLEAN_CONTACT_ID,
        f"The caller quoted NRIC {sample_cases.PLANTED_NRIC} on the line.",
        tenant=sample_cases.TENANT,
        settings=local_settings(),
    )
    assert sample_cases.PLANTED_NRIC not in repr(result)


def test_the_audit_verification_tool_reports_an_honest_verdict() -> None:
    report = verify_audit_trail(local_settings())
    assert report["ok"] is True
    assert report["anchored"] is False, "the ephemeral gate store has no external anchor"


def test_the_audit_verification_tool_refuses_where_it_cannot_verify() -> None:
    """A managed WORM sink is not verified from here, and saying "ok" would be a false claim."""
    with pytest.raises(NotImplementedError):
        verify_audit_trail(Settings(profile="onprem"))


def test_the_tools_import_and_run_with_no_agent_runtime_installed(no_cloud_sdk: None) -> None:
    """``build_function_tools`` is the ONLY code path that may need a runtime."""
    from contact_centre_conversations.agent import tools

    assert tools.TOOL_FUNCTIONS
    assert build_agent_card(local_settings()).skills
    with pytest.raises(ModuleNotFoundError):
        tools.build_function_tools()
