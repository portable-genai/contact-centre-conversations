"""``--mode gate`` is the promotion path, and until now nothing exercised a line of it.

The offline smoke rubrics were well covered and ``run_gate`` was not covered at all: not the
profile refusal, not the three-state service URL, not which bundle each mode asks about, and not
what happens when the authority answers with something incomplete. That is the half of the eval
that decides whether a release may ship, so it is the half most worth proving.

Everything here is offline. ``respx`` mocks the wire, so no test needs a reachable Hrz4, and the
bodies are the COMPLETE attested GateDecision the client demands rather than a naked
``{"passed": true}``. Building the real shape is deliberate: a fixture that only had to satisfy
a boolean would let the client's evidence requirements rot without anything noticing.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx
import run_eval
from agent_eval_kit.gate_client import GateClientError

_LOOPBACK = "http://localhost:8084"


def _eval_report(*, attested: bool = True, refs: bool = True) -> dict[str, Any]:
    """The evidence half: per-metric rows plus the durable identifiers the client insists on."""
    return {
        "results": [{"metric": "gate_precision", "score": 1.0, "threshold": 1.0, "passed": True}],
        "n_examples": 10,
        "run_id": "run-fictional-0001",
        "dataset_digest": "0" * 64,
        "evaluator": "hrz4-quality-gate",
        "artifact_refs": ["gs://fictional-hrz4-evidence/run-fictional-0001/report.json"]
        if refs
        else [],
        "attested": attested,
    }


def _decision(*, passed: bool = True, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "passed": passed,
        "eval_report": _eval_report(),
        "redteam_report": {
            "passed": passed,
            "results": [{"name": "prompt-injection", "passed": passed, "blocked": passed}],
        },
        "model_card_ref": "gs://fictional-hrz4-evidence/model-card.md",
        "mrm_evidence_ref": "gs://fictional-hrz4-evidence/mrm.pdf",
    }
    body.update(overrides)
    return body


def _managed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The managed profile, which is the only one `--mode gate` will run under."""
    monkeypatch.setenv("CONTACT_PROFILE", "gcp")
    monkeypatch.delenv("CONTACT_QUALITY_URL", raising=False)


# ------------------------------------------------------------------ the profile refusal
@pytest.mark.parametrize("profile", ["local", "onprem"])
def test_gate_mode_refuses_off_the_managed_profile(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    """A promotion verdict from a laptop is not a promotion verdict."""
    monkeypatch.setenv("CONTACT_PROFILE", profile)
    with pytest.raises(SystemExit, match="requires CONTACT_PROFILE=gcp"):
        run_eval.run_gate(run_eval.SELF_SERVICE, run_eval.DATASETS[run_eval.SELF_SERVICE])


def test_the_refusal_names_the_offline_check_so_the_reader_knows_what_to_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTACT_PROFILE", "local")
    with pytest.raises(SystemExit, match="--mode smoke"):
        run_eval.run_gate(run_eval.AGENT_ASSIST, run_eval.DATASETS[run_eval.AGENT_ASSIST])


# ------------------------------------------------------------------ the service URL, three states
def test_an_unset_quality_url_takes_the_documented_loopback_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTACT_QUALITY_URL", raising=False)
    assert run_eval._quality_url() == _LOOPBACK


def test_an_emptied_quality_url_refuses_rather_than_inheriting_that_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middle state. An operator who emptied it named nothing, and nothing is not localhost."""
    monkeypatch.setenv("CONTACT_QUALITY_URL", "")
    with pytest.raises(SystemExit, match="set but empty"):
        run_eval._quality_url()


def test_a_named_quality_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTACT_QUALITY_URL", "https://quality.example")
    assert run_eval._quality_url() == "https://quality.example"


def test_a_plain_http_service_off_loopback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """Promotion evidence does not travel in clear text to somewhere that is not this machine."""
    _managed(monkeypatch)
    monkeypatch.setenv("CONTACT_QUALITY_URL", "http://quality.example")
    with pytest.raises(Exception, match="https|secure|loopback"):
        run_eval.run_gate(run_eval.SELF_SERVICE, run_eval.DATASETS[run_eval.SELF_SERVICE])


# ------------------------------------------------------------------ the happy path
@respx.mock
def test_a_passing_authority_makes_the_run_exit_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_report())
    )
    respx.post(f"{_LOOPBACK}/v1/gate").mock(return_value=httpx.Response(200, json=_decision()))
    assert run_eval.main(["--mode", "gate", "--rubric", run_eval.SELF_SERVICE]) == 0


@respx.mock
def test_a_failing_authority_makes_the_run_exit_non_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of asking. A gate whose FAIL did not reach the exit code gates nothing."""
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_report())
    )
    respx.post(f"{_LOOPBACK}/v1/gate").mock(
        return_value=httpx.Response(200, json=_decision(passed=False))
    )
    assert run_eval.main(["--mode", "gate", "--rubric", run_eval.SELF_SERVICE]) == 1


# ------------------------------------------------------------------ bundle selection
@respx.mock
@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_each_mode_asks_about_its_own_bundle(monkeypatch: pytest.MonkeyPatch, rubric: str) -> None:
    """Two modes are two promotions. Asking one bundle about both would blend the verdicts."""
    _managed(monkeypatch)
    evaluations = respx.post(f"{_LOOPBACK}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_report())
    )
    gate = respx.post(f"{_LOOPBACK}/v1/gate").mock(
        return_value=httpx.Response(200, json=_decision())
    )
    run_eval.main(["--mode", "gate", "--rubric", rubric])

    expected = run_eval.BUNDLES[rubric]
    for route in (evaluations, gate):
        body = json.loads(route.calls.last.request.content)
        assert body["bundle"] == expected, f"{rubric} asked about {body['bundle']}"


@respx.mock
def test_the_authority_is_told_which_dataset_was_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_report())
    )
    route = respx.post(f"{_LOOPBACK}/v1/gate").mock(
        return_value=httpx.Response(200, json=_decision())
    )
    run_eval.main(["--mode", "gate", "--rubric", run_eval.SELF_SERVICE])
    body = json.loads(route.calls.last.request.content)
    assert body["target"]["dataset_id"] == run_eval.SCENARIOS.name


# ------------------------------------------------------------- incomplete evidence fails closed
@respx.mock
@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("unattested", _decision(eval_report=_eval_report(attested=False))),
        ("no artifact refs", _decision(eval_report=_eval_report(refs=False))),
        ("no redteam evidence", _decision(redteam_report={})),
        (
            "contradictory redteam row",
            _decision(
                redteam_report={
                    "passed": True,
                    "results": [{"name": "x", "passed": True, "blocked": False}],
                }
            ),
        ),
        ("no model card", _decision(model_card_ref="")),
        ("no mrm evidence", _decision(mrm_evidence_ref="")),
        (
            "aggregate contradicts its own evidence",
            _decision(
                passed=True,
                redteam_report={
                    "passed": False,
                    "results": [{"name": "x", "passed": False, "blocked": False}],
                },
            ),
        ),
    ],
)
def test_incomplete_promotion_evidence_raises_rather_than_passing(
    monkeypatch: pytest.MonkeyPatch, label: str, body: dict[str, Any]
) -> None:
    """Each of these is a way a service could say yes while proving nothing.

    They must RAISE rather than resolve to False: a refusal the caller can read as an ordinary
    negative verdict is a promotion path that fails open the moment somebody retries it.
    """
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(
        return_value=httpx.Response(200, json=_eval_report())
    )
    respx.post(f"{_LOOPBACK}/v1/gate").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(GateClientError):
        run_eval.run_gate(run_eval.SELF_SERVICE, run_eval.DATASETS[run_eval.SELF_SERVICE])


@respx.mock
def test_an_unreachable_authority_raises_rather_than_reporting_a_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "The gate said no" and "we could not ask" are different facts and only one is a verdict."""
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(GateClientError, match="failed"):
        run_eval.run_gate(run_eval.SELF_SERVICE, run_eval.DATASETS[run_eval.SELF_SERVICE])


@respx.mock
def test_a_server_error_is_not_read_as_a_negative_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _managed(monkeypatch)
    respx.post(f"{_LOOPBACK}/v1/evaluations").mock(return_value=httpx.Response(503, text="down"))
    with pytest.raises(GateClientError, match="503"):
        run_eval.run_gate(run_eval.SELF_SERVICE, run_eval.DATASETS[run_eval.SELF_SERVICE])
