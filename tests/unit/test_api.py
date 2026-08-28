"""API surface: two mode routes, the mode gate, verified-principal identity, fail-closed S2S.

The client comes from the shared ``api_client`` fixture, which pins a loopback peer: the
app-object exposure guard refuses the unauthenticated local posture to any other peer, and
TestClient's default peer is the literal host "testclient".
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from tests.fixtures import sample_cases

_TOKEN_ENV = "CONTACT_S2S_TOKEN"
_PERSONA = {"X-Dev-Persona": "auditor"}


def _turn(text: str, *, index: int = 0, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "contact_id": sample_cases.CLEAN_CONTACT_ID,
        "market": sample_cases.MARKET,
        "locale": sample_cases.LOCALE,
        "vertical": sample_cases.VERTICAL,
        "text": text,
        "index": index,
    }
    body.update(extra)
    return body


def test_the_agent_assist_route_returns_a_deterministic_panel(api_client: TestClient) -> None:
    resp = api_client.post(
        "/v1/agent-assist/turn",
        json=_turn(
            "Thank you for calling. This call is being recorded for quality.",
            speaker_id="agent-1",
            role="agent",
            start_ms=0,
            end_ms=6000,
        ),
        headers=_PERSONA,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "agent_assist"
    assert body["state_id"], "the panel must name the procedure state it computed"
    assert body["next_step"]["instruction"], "a whisper panel with no next step shows nothing"
    assert body["next_step"]["citations"], "every deterministic claim carries provenance"


def test_the_self_service_route_denies_an_out_of_scope_ask(api_client: TestClient) -> None:
    resp = api_client.post(
        "/v1/self-service/turn",
        json=_turn(
            "Please refinance my mortgage and tell me which fund to buy.",
            contact_id=sample_cases.SELF_SERVICE_CONTACT_ID,
            channel="chat",
        ),
        headers=_PERSONA,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"]["outcome"] == "deny"
    assert body["handoff"]["trigger"] == "gate_denial"
    assert body["suggestion"] is None, "a denied turn must not reach a model at all"


def test_a_denied_turn_carries_the_reason_codes_a_reviewer_can_group_by(
    api_client: TestClient,
) -> None:
    body = api_client.post(
        "/v1/self-service/turn",
        json=_turn(
            "Sell my house please.",
            contact_id=sample_cases.SELF_SERVICE_CONTACT_ID,
        ),
        headers=_PERSONA,
    ).json()
    assert [reason["code"] for reason in body["verdict"]["reasons"]] == ["no_intent_match"]


def test_unknown_persona_is_401(api_client: TestClient) -> None:
    resp = api_client.post(
        "/v1/agent-assist/turn", json=_turn("hello"), headers={"X-Dev-Persona": "ghost"}
    )
    assert resp.status_code == 401


def test_healthz_reports_profile_region_and_the_mode_posture(api_client: TestClient) -> None:
    body = api_client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["profile"] == "local"
    assert body["region"] == "asia-southeast1"
    reported = {mode["mode"]: mode["enabled"] for mode in body["modes"]}
    assert reported == {"agent_assist": True, "self_service": True}, (
        "healthz must report which modes this deployment serves: 'up but every mode route "
        "answers 503' is the most confusing state this design can be in"
    )


def test_security_headers_present(api_client: TestClient) -> None:
    headers = api_client.get("/healthz").headers
    assert headers["Content-Security-Policy"] == "frame-ancestors 'self'"
    assert headers["X-Content-Type-Options"] == "nosniff"


@pytest.fixture()
def token_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(_TOKEN_ENV, "s3cret-service-token")
    yield "s3cret-service-token"


def test_s2s_endpoint_open_when_secret_unset(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(_TOKEN_ENV, raising=False)
    assert api_client.post("/v1/audit/ping").status_code == 200


def test_s2s_endpoint_rejects_missing_token_when_enforced(
    api_client: TestClient, token_env: str
) -> None:
    assert api_client.post("/v1/audit/ping").status_code == 401


def test_s2s_endpoint_accepts_correct_token(api_client: TestClient, token_env: str) -> None:
    resp = api_client.post("/v1/audit/ping", headers={"Authorization": f"Bearer {token_env}"})
    assert resp.status_code == 200
