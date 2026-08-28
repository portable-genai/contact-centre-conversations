"""A verdict over nothing is not a pass: the promotion report fails closed (baseline check E4).

The named historical defect this exists for is an evaluation that returned "passed" over an
empty metric list. ``all(())`` is mathematically true and is not evidence. The same shape
appears one step out: a run that scored zero examples, or a dataset that could not be read, both
produce a report with nothing in it, and both must fail rather than sail through.

``agent_eval_kit.EvalReport.passed`` already requires ``n_examples > 0`` and a non-empty result
set. That is the commons doing the work, and this module is the proof it still does AND that
this repo actually fills the fields it depends on. A runner that reported the right metrics over
a hardcoded ``n_examples=0`` would satisfy the commons' type and gate nothing, which is exactly
what the fleet scanner greps for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import run_eval
from agent_eval_kit import EvalMetricResult, EvalReport


def _perfect(metric: str = "groundedness") -> EvalMetricResult:
    return EvalMetricResult.scored(metric, 1.0, 1.0)


# ------------------------------------------------------------------ the empty shapes
def test_a_report_with_no_metrics_does_not_pass() -> None:
    """The original defect, in one line. `all(())` is True and asserts nothing."""
    assert EvalReport(dataset="d", results=(), n_examples=10).passed is False


def test_a_report_over_zero_examples_does_not_pass() -> None:
    """Perfect scores over nothing at all. Every metric is 1.0 and none of them measured a case."""
    assert EvalReport(dataset="d", results=(_perfect(),), n_examples=0).passed is False


def test_a_report_with_neither_does_not_pass() -> None:
    assert EvalReport(dataset="d", results=(), n_examples=0).passed is False


def test_a_real_report_still_passes() -> None:
    """Without this the three above are satisfied by a `passed` that is always False."""
    assert EvalReport(dataset="d", results=(_perfect(),), n_examples=1).passed is True


def test_one_failing_metric_fails_the_whole_report() -> None:
    failing = EvalMetricResult.scored("gate_precision", 0.9, 1.0)
    assert EvalReport(dataset="d", results=(_perfect(), failing), n_examples=5).passed is False


# ------------------------------------------------------------------ this repo fills the fields
@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_the_runner_reports_the_dataset_s_own_example_count(rubric: str) -> None:
    """The load-bearing field: a runner reporting zero would gate nothing and look green."""
    dataset = run_eval.DATASETS[rubric]
    report = run_eval.SMOKE[rubric](dataset)
    assert report.n_examples == len(run_eval.load_cases(dataset))
    assert report.n_examples > 0


@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_the_report_names_the_bytes_it_scored_and_what_scored_them(rubric: str) -> None:
    report = run_eval.SMOKE[rubric](run_eval.DATASETS[rubric])
    assert report.dataset_digest == run_eval.dataset_digest(run_eval.DATASETS[rubric])
    assert report.evaluator == run_eval.EVALUATOR
    assert report.run_id.startswith(rubric)


def test_two_datasets_with_different_bytes_get_different_digests(tmp_path: Path) -> None:
    """Or the digest is decoration and two reports could claim the same provenance."""
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text('{"id": "x"}\n', encoding="utf-8")
    second.write_text('{"id": "y"}\n', encoding="utf-8")
    assert run_eval.dataset_digest(first) != run_eval.dataset_digest(second)


# ------------------------------------------------------------------ the dataset side
def test_an_empty_dataset_is_refused_rather_than_scored_as_perfect(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="golden dataset is empty"):
        run_eval.load_cases(empty)


def test_a_dataset_of_only_comments_is_empty_too(tmp_path: Path) -> None:
    """A file that LOOKS authored and contains no cases is the worse version of the above."""
    commented = tmp_path / "comments.jsonl"
    commented.write_text("# every case was removed\n#\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="golden dataset is empty"):
        run_eval.load_cases(commented)


def test_an_unreadable_dataset_fails_the_run_rather_than_counting_zero(tmp_path: Path) -> None:
    """Counting zero and passing is precisely the fail-open this check exists to remove."""
    assert (
        run_eval.main(["--rubric", run_eval.SELF_SERVICE, "--dataset", str(tmp_path / "no")]) == 2
    )


def test_the_cli_exits_non_zero_when_a_rubric_fails(tmp_path: Path) -> None:
    """The verdict has to reach the exit code, or nothing in CI notices it."""
    rows = run_eval.load_cases(run_eval.DATASETS[run_eval.SELF_SERVICE])
    for row in rows:
        row["expected_outcome"] = "allow"
    broken = tmp_path / "broken.jsonl"
    broken.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert run_eval.main(["--rubric", run_eval.SELF_SERVICE, "--dataset", str(broken)]) == 1
