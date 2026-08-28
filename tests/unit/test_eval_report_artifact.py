"""The reviewer's report: per-conversation detail, and a page that survives being emailed.

The kit's ``EvalReport`` is per-metric and frozen, so the drill-down lives in a companion
artifact this repository owns. These tests hold the two properties that make it useful rather
than merely present: the index carries no transcript text, and the page is self-contained.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import report_artifact
import run_eval

from tests import REPO_ROOT

_RENDERER = REPO_ROOT / "scripts" / "render_eval_report.py"


@pytest.fixture(scope="module")
def artifact(tmp_path_factory: pytest.TempPathFactory) -> dict:
    target = tmp_path_factory.mktemp("report") / "report.json"
    assert run_eval.main(["--emit", str(target)]) == 0
    return json.loads(target.read_text(encoding="utf-8"))


def test_every_scenario_appears_as_a_case(artifact: dict) -> None:
    for run in artifact["runs"]:
        expected = len(run_eval.load_scenarios(run_eval.DATASETS[run["rubric"]], run["rubric"]))
        assert len(run["cases"]) == expected
        assert len(run["rows"]) == expected


def test_a_case_carries_what_was_said_and_what_was_expected(artifact: dict) -> None:
    """A drill-down showing only the outcome sends a reviewer to another window to judge it."""
    run = next(r for r in artifact["runs"] if r["rubric"] == run_eval.SELF_SERVICE)
    case = next(c for c in run["cases"] if c["turns"])
    turn = case["turns"][0]
    assert turn["text"].strip()
    assert turn["expected"] and turn["actual"]


def test_the_index_row_carries_no_transcript_text(artifact: dict) -> None:
    """E3's rule, inherited: an index is what gets exported, pasted into a ticket and kept.

    Shipping utterances into it is how a quality programme becomes a data-protection incident.
    The words live in the detail, where somebody chose to look at one conversation.
    """
    said = {
        turn["text"] for run in artifact["runs"] for case in run["cases"] for turn in case["turns"]
    }
    for run in artifact["runs"]:
        for row in run["rows"]:
            flat = json.dumps(row, ensure_ascii=False)
            assert not any(text and text in flat for text in said), row["case_id"]


def test_the_artifact_names_the_corpus_it_scored(artifact: dict) -> None:
    for run in artifact["runs"]:
        assert run["dataset_digest"]
        assert run["run_id"]
        assert run["schema_version"] == report_artifact.SCHEMA_VERSION


def test_a_failing_metric_carries_something_to_change(artifact: dict) -> None:
    """A red row with no next step gets argued about rather than fixed."""
    for run in artifact["runs"]:
        for metric in run["metrics"]:
            assert metric["passed"] or metric["remediation"], metric["metric"]


def test_the_run_level_compliance_metrics_are_attributed_to_their_cases(artifact: dict) -> None:
    """A red rollup must point at a conversation, or the reviewer diffs the whole corpus.

    Party isolation, citation audience and injection handling are aggregated across the run,
    but each is EXERCISED by specific conversations, and those conversations must carry the
    per-case verdict so a failure is findable one click deep rather than nowhere.
    """
    run = next(r for r in artifact["runs"] if r["rubric"] == run_eval.SELF_SERVICE)
    seen = {d["metric"] for case in run["cases"] for d in case["dimensions"]}
    for metric in (
        "customer_party_isolation_safety",
        "customer_citation_audience_safety",
        "injection_handling_safety",
    ):
        assert metric in seen, f"no case carries a per-case verdict for {metric}"


def test_an_empty_artifact_list_is_refused_rather_than_written(tmp_path: Path) -> None:
    """A report file with zero runs still parses, and a renderer summing nothing over it would
    paint zero failures as a pass. The absence of runs is a caller bug, not a result."""
    target = tmp_path / "report.json"
    with pytest.raises(ValueError, match="no run artifacts"):
        report_artifact.write_artifact([], target)
    assert not target.exists()


# ------------------------------------------------------------------ the page
def _render(payload: dict, tmp_path: Path) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "report.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(_RENDERER), str(source), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "wrote" in result.stdout
    return (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_the_page_is_self_contained(artifact: dict, tmp_path: Path) -> None:
    """One file that works from a mail attachment on a locked-down laptop, or it is not a report."""
    page = _render(artifact, tmp_path)
    assert "<style>" in page
    assert 'src="http' not in page and 'href="http' not in page
    assert "<script" not in page


def test_every_conversation_is_collapsible(artifact: dict, tmp_path: Path) -> None:
    page = _render(artifact, tmp_path)
    cases = sum(len(run["cases"]) for run in artifact["runs"])
    assert page.count("<details") == cases


def test_failing_conversations_open_and_passing_ones_do_not(artifact: dict, tmp_path: Path) -> None:
    """Thirty conversations expanded is a wall nobody reads; the failures are what to look at."""
    clean = _render(artifact, tmp_path / "clean")
    assert "<details open" not in clean

    broken = json.loads(json.dumps(artifact))
    case = broken["runs"][1]["cases"][0]
    case["dimensions"][0]["passed"] = False
    broken["runs"][1]["rows"][0]["passed"] = False
    page = _render(broken, tmp_path / "broken")
    assert page.count("<details open") == 1


def test_a_report_with_no_runs_is_refused_by_the_renderer(tmp_path: Path) -> None:
    """Defence in depth behind the writer's refusal: a hand-fed empty report must not paint."""
    source = tmp_path / "empty.json"
    source.write_text(
        json.dumps({"schema_version": report_artifact.SCHEMA_VERSION, "runs": []}),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(_RENDERER), str(source), str(tmp_path / "out")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "no runs" in result.stderr
    assert not (tmp_path / "out" / "index.html").exists()


def test_the_page_escapes_what_a_customer_said(tmp_path: Path) -> None:
    """Turn text is untrusted input that reaches a browser. It is escaped, not interpolated."""
    payload = {
        "schema_version": report_artifact.SCHEMA_VERSION,
        "runs": [
            {
                "schema_version": report_artifact.SCHEMA_VERSION,
                "run_id": "r",
                "rubric": "self_service",
                "dataset": "d",
                "dataset_digest": "0" * 64,
                "evaluator": "e",
                "as_of": "2026-08-08T09:00:00+00:00",
                "metrics": [
                    {
                        "metric": "gate_precision",
                        "score": 1.0,
                        "threshold": 1.0,
                        "passed": True,
                        "detail": "",
                        "remediation": "",
                    }
                ],
                "rows": [
                    {
                        "case_id": "x",
                        "rubric": "self_service",
                        "family": "benign",
                        "market": "SG",
                        "vertical": "retail_banking",
                        "tenant": "demo-bank",
                        "turn_count": 1,
                        "passed": True,
                        "failing_metrics": [],
                    }
                ],
                "cases": [
                    {
                        "case_id": "x",
                        "rubric": "self_service",
                        "family": "benign",
                        "market": "SG",
                        "vertical": "retail_banking",
                        "tenant": "demo-bank",
                        "party_ref": "p",
                        "note": "",
                        "turns": [
                            {
                                "index": 0,
                                "text": "<script>alert(1)</script>",
                                "expected": {},
                                "actual": {},
                                "citations": [],
                                "notes": [],
                            }
                        ],
                        "dimensions": [],
                    }
                ],
            }
        ],
    }
    page = _render(payload, tmp_path)
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
