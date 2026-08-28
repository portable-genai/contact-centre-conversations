"""Replayed model output is scored offline, and a missing recording is loud rather than quiet.

Every deterministic metric scores the offline template drafter, which structurally cannot invent
a figure, so `citation_accuracy` and `groundedness` measure the VALIDATOR rather than a model's
restraint. That is the largest honest gap in the suite and the model card says so.

Closing it cannot mean calling a model in the gate: the gate must pass with no network, no
credentials and no cloud SDK. So a real call happens once, by hand, and its output is committed
and replayed. These tests hold the property that makes a replay evidence rather than decoration:
it FAILS rather than substituting something else. A replay that quietly fell back to the template
drafter would report a score for a model that produced none of it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import replay_generation
import run_eval

from contact_centre_conversations.domain.kernel import Citation
from contact_centre_conversations.domain.models import RetrievedPassage


def _passage(source_id: str = "kb-sg-001") -> RetrievedPassage:
    return RetrievedPassage(
        text="The balance quoted is the posted balance.",
        citation=Citation(source_id=source_id, title="Card balance", source_ref="ref"),
        audience="public",
    )


def _fixture(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "gemini_replay.jsonl"
    path.write_text(
        "# recorded\n" + "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def test_the_key_covers_the_model_the_prompt_and_the_passages() -> None:
    """All three change the answer, so keying on fewer would replay the wrong reply as evidence."""
    base = replay_generation.recording_key("m", "p", (_passage(),))
    assert base != replay_generation.recording_key("other", "p", (_passage(),))
    assert base != replay_generation.recording_key("m", "other", (_passage(),))
    assert base != replay_generation.recording_key("m", "p", (_passage("kb-sg-002"),))


def test_the_key_is_stable_across_passage_order() -> None:
    """Retrieval order is a ranking detail; the same passages are the same input."""
    first, second = _passage("kb-sg-001"), _passage("kb-sg-002")
    assert replay_generation.recording_key("m", "p", (first, second)) == (
        replay_generation.recording_key("m", "p", (second, first))
    )


def test_a_missing_recording_raises_rather_than_falling_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """THE property. A silent fallback would score a model that produced none of the text."""
    monkeypatch.setattr(replay_generation, "FIXTURE", _fixture(tmp_path, [{"key": "other"}]))
    adapter = replay_generation.ReplayGenerationAdapter(run_eval.eval_settings())
    with pytest.raises(RuntimeError, match="no recorded model output for"):
        adapter.draft("what is my card balance", (_passage(),))


def test_an_absent_fixture_names_how_to_produce_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(replay_generation, "FIXTURE", tmp_path / "absent.jsonl")
    adapter = replay_generation.ReplayGenerationAdapter(run_eval.eval_settings())
    with pytest.raises(RuntimeError, match="record_gemini_fixtures.py"):
        adapter.draft("anything", (_passage(),))


def test_an_empty_fixture_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("# every recording was removed\n", encoding="utf-8")
    monkeypatch.setattr(replay_generation, "FIXTURE", path)
    adapter = replay_generation.ReplayGenerationAdapter(run_eval.eval_settings())
    with pytest.raises(RuntimeError, match="contains no recordings"):
        adapter.draft("anything", (_passage(),))


def test_a_recorded_reply_is_replayed_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without this the refusals above are satisfied by an adapter that refuses everything."""
    settings = run_eval.eval_settings()
    passages = (_passage(),)
    prompt = "what is my card balance"
    key = replay_generation.recording_key(settings.model, prompt, passages)
    recorded = {"text": "The balance shown is the posted balance.", "passage_ids": ["kb-sg-001"]}
    monkeypatch.setattr(
        replay_generation, "FIXTURE", _fixture(tmp_path, [{"key": key, "response": recorded}])
    )
    adapter = replay_generation.ReplayGenerationAdapter(settings)
    assert adapter.draft(prompt, passages) == recorded


def test_a_recorded_refusal_replays_as_no_suggestion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A model that declined is a real answer worth scoring, not an error to hide."""
    settings = run_eval.eval_settings()
    passages = (_passage(),)
    key = replay_generation.recording_key(settings.model, "p", passages)
    monkeypatch.setattr(
        replay_generation, "FIXTURE", _fixture(tmp_path, [{"key": key, "response": None}])
    )
    adapter = replay_generation.ReplayGenerationAdapter(settings)
    assert adapter.draft("p", passages) is None


def test_the_runner_refuses_the_replay_drafter_without_a_recording() -> None:
    """The shipped state: no recording is committed, so the flag says how to make one."""
    assert not replay_generation.FIXTURE.exists(), (
        "a recording is committed; update this test to assert it is used instead"
    )
    with pytest.raises(SystemExit, match="record_gemini_fixtures.py"):
        run_eval.main(["--drafter", "replay-gemini"])


def test_the_replay_adapter_is_not_bound_by_any_shipped_profile() -> None:
    """Nothing a deployment binds may serve pre-recorded answers to a customer.

    The adapter lives under `eval/` and is bound only by the eval's own settings override, so a
    misconfigured deployment cannot reach it: there is no binding to misconfigure.
    """
    from contact_centre_conversations.config import DEFAULT_BINDINGS

    for profile, target in DEFAULT_BINDINGS["generation"].items():
        assert "replay" not in target.lower(), f"{profile} binds a replay drafter"
