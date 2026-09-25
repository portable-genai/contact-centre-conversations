"""The service half of the model pills: which model ANSWERED, and whether it searched.

The console shows two pills at the top right: the model that answered the last request, and
``Search`` when that answer used an online search tool. Both come from response headers the kit
emits (``install_answer_provenance`` in ``api/app.py``) for whatever the model adapters NOTED as
they called. Before a request is answered the pill shows ``generator_model`` from ``/healthz``,
so that value must be the model the bound adapter calls, never one a configuration flag names
while the adapter calls another.

Three drafters answer here: the offline template drafter (notes the stub ``generator_model``
names under ``local``), the managed Gemini drafter (notes the model it called, proved through a
FAKE ``google.genai``), and the eval's replay drafter, which notes the model the committed
recording was captured from, because those are that model's words.
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import replay_generation
import run_eval
from fastapi.testclient import TestClient
from hex_service_kit import provenance

from contact_centre_conversations import config
from contact_centre_conversations.adapters.gcp.generation import VertexGenerationAdapter
from contact_centre_conversations.adapters.local.generation import (
    STUB_MODEL,
    LocalTemplateGenerationAdapter,
)
from contact_centre_conversations.api import app as app_module
from contact_centre_conversations.domain.kernel import Citation
from contact_centre_conversations.domain.models import RetrievedPassage

from tests import REPO_ROOT
from tests.conftest import local_settings
from tests.unit.test_api import _turn

ANSWERED_BY = "x-answered-by"
SEARCH_USED = "x-search-used"
_ASK = "What is my card balance please?"


@pytest.fixture()
def local_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The API under ``local`` whatever the shell exported: CI runs with no profile set."""
    monkeypatch.setenv(config._PROFILE_ENV, "local")
    app_module._container.cache_clear()
    with TestClient(app_module.app, client=("127.0.0.1", 50000)) as client:
        yield client
    app_module._container.cache_clear()


def _assist(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/agent-assist/turn",
        json=_turn(_ASK, speaker_id="customer", role="customer"),
        headers={"X-Dev-Persona": "auditor"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["suggestion"], "the turn never reached the drafter"
    return dict(response.headers)


def _passage() -> RetrievedPassage:
    return RetrievedPassage(
        text="The balance quoted is the posted balance.",
        citation=Citation(source_id="kb-sg-001", title="Card balance", source_ref="ref"),
        audience="public",
    )


def test_the_template_drafter_answers_as_the_stub_the_pill_first_names(
    local_client: TestClient,
) -> None:
    headers = _assist(local_client)
    assert headers[ANSWERED_BY] == STUB_MODEL
    assert SEARCH_USED not in headers
    assert local_settings().generator_model == STUB_MODEL


def test_a_call_that_searched_says_so_and_the_next_request_starts_fresh(
    local_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = LocalTemplateGenerationAdapter.draft

    def searching(self: LocalTemplateGenerationAdapter, prompt: str, passages: Any) -> Any:
        provenance.note_model("fake-searching-model")
        provenance.note_search()
        return original(self, prompt, passages)

    monkeypatch.setattr(LocalTemplateGenerationAdapter, "draft", searching)
    headers = _assist(local_client)
    assert headers[ANSWERED_BY] == f"fake-searching-model, {STUB_MODEL}"
    assert headers[SEARCH_USED] == "true"
    monkeypatch.setattr(LocalTemplateGenerationAdapter, "draft", original)
    headers = _assist(local_client)
    assert headers[ANSWERED_BY] == STUB_MODEL
    assert SEARCH_USED not in headers


def test_a_drafter_asked_nothing_notes_nothing() -> None:
    """No passages, no model call: the pill must not name a model that was never asked."""
    with provenance.scope() as record:
        assert LocalTemplateGenerationAdapter(local_settings()).draft(_ASK, ()) is None
    assert record.models == []


# --------------------------------------------------------------------------------------- #
# The replay drafter names the RECORDED model.
# --------------------------------------------------------------------------------------- #
def test_the_replay_drafter_notes_the_model_the_recording_was_captured_from(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = run_eval.eval_settings()
    passages = (_passage(),)
    key = replay_generation.recording_key(settings.model, _ASK, passages)
    row = {"key": key, "model": "the-recorded-model", "response": {"text": "t", "passage_ids": []}}
    fixture = tmp_path / "gemini_replay.jsonl"
    fixture.write_text("# recorded\n" + json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setattr(replay_generation, "FIXTURE", fixture)
    with provenance.scope() as record:
        replay_generation.ReplayGenerationAdapter(settings).draft(_ASK, passages)
    assert record.models == ["the-recorded-model"]
    assert record.search_used is False


def test_the_committed_replay_names_the_model_the_managed_drafter_calls() -> None:
    """The pill under replay and the pill under ``gcp`` must name the same model."""
    rows = [
        json.loads(line)
        for line in replay_generation.FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    gcp = dataclasses.replace(local_settings(), profile="gcp")
    assert {row["model"] for row in rows} == {gcp.generator_model}


# --------------------------------------------------------------------------------------- #
# The managed Gemini drafter, through a fake SDK.
# --------------------------------------------------------------------------------------- #
class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(parsed={"text": "t", "passage_ids": ["kb-sg-001"]})


@pytest.fixture()
def fake_genai(monkeypatch: pytest.MonkeyPatch) -> _FakeModels:
    models = _FakeModels()
    genai = types.ModuleType("google.genai")
    genai_types = types.ModuleType("google.genai.types")
    genai_types.GenerateContentConfig = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    genai_types.ThinkingConfig = lambda **kw: SimpleNamespace(**kw)  # type: ignore[attr-defined]
    genai.types = genai_types  # type: ignore[attr-defined]
    genai.Client = lambda **_: SimpleNamespace(models=models)  # type: ignore[attr-defined]
    google = sys.modules.get("google") or types.ModuleType("google")
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setattr(google, "genai", genai, raising=False)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", genai_types)
    return models


def test_the_gemini_drafter_notes_the_model_it_called_and_stays_pinned(
    fake_genai: _FakeModels,
) -> None:
    """Pinned on purpose: the committed replay was recorded at 0.0 and the eval scores it."""
    settings = dataclasses.replace(local_settings(), profile="gcp")
    with provenance.scope() as record:
        VertexGenerationAdapter(settings).draft(_ASK, (_passage(),))
    assert record.models == [settings.model]
    assert record.search_used is False
    (call,) = fake_genai.calls
    assert call["model"] == settings.model == settings.generator_model
    assert call["config"].temperature == 0.0
    assert not hasattr(call["config"], "tools"), "no online search tool is attached"


def test_the_gemini_drafter_asked_nothing_calls_and_notes_nothing(
    fake_genai: _FakeModels,
) -> None:
    settings = dataclasses.replace(local_settings(), profile="gcp")
    with provenance.scope() as record:
        assert VertexGenerationAdapter(settings).draft(_ASK, ()) is None
    assert record.models == [] and fake_genai.calls == []


# --------------------------------------------------------------------------------------- #
# generator_model is the model the adapter calls.
# --------------------------------------------------------------------------------------- #
def test_no_flag_swaps_in_a_model_the_adapter_never_calls() -> None:
    """The latent false banner: a flag that moved the pill but not the model that answered."""
    models = SimpleNamespace(
        reasoning="the-model-the-adapter-calls",
        hard_reasoning="a-model-nobody-calls",
        use_hard_reasoning=True,
    )
    named = config._model_from_settings(SimpleNamespace(models=models), "models.reasoning")
    assert named == "the-model-the-adapter-calls"


def test_the_hard_reasoning_flag_does_not_exist() -> None:
    settings_file = (REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "use_hard_reasoning" not in settings_file
    for source in sorted((REPO_ROOT / "src").rglob("*.py")):
        assert "use_hard_reasoning" not in source.read_text(encoding="utf-8"), source
