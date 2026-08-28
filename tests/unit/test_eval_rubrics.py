"""Every bar has exactly one home, and it is a reviewed file rather than a Python literal.

Thresholds used to be a dict in ``eval/run_eval.py``. That sat oddly against this repo's own
practice B4, which says bank-owned policy numbers are configuration: every other threshold here
is a pack. It also put the number somewhere a reviewer would not look, with no room for the
reasoning that justifies it.

The rubrics are now the source. This module holds the property that makes that true rather than
decorative: the set of metrics the runners EMIT and the set the rubrics DECLARE must be equal.
One direction catches a metric scored against a bar nobody reviewed. The other catches a bar
that survived the metric it gated, which is how a rubric directory fills up with numbers that
mean nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import run_eval
import yaml

_RUBRIC_ROOT = Path(run_eval.__file__).resolve().parent / "rubrics"


def _emitted(rubric: str) -> set[str]:
    report = run_eval.SMOKE[rubric](run_eval.DATASETS[rubric])
    return {result.metric for result in report.results}


@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_every_emitted_metric_has_a_reviewed_threshold(rubric: str) -> None:
    missing = _emitted(rubric) - set(run_eval.THRESHOLDS[rubric])
    assert not missing, f"{rubric}: metrics scored against no reviewed bar: {sorted(missing)}"


@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_every_reviewed_threshold_is_actually_measured(rubric: str) -> None:
    """The direction that rots quietly: a bar outliving the metric it gated."""
    unused = set(run_eval.THRESHOLDS[rubric]) - _emitted(rubric)
    assert not unused, f"{rubric}: thresholds nothing measures: {sorted(unused)}"


@pytest.mark.parametrize("rubric", run_eval.RUBRICS)
def test_the_report_carries_the_rubric_bar_and_not_some_other_number(rubric: str) -> None:
    """A loaded threshold that never reached the report would be documentation, not a gate."""
    report = run_eval.SMOKE[rubric](run_eval.DATASETS[rubric])
    for result in report.results:
        assert result.threshold == run_eval.THRESHOLDS[rubric][result.metric]


def test_every_rubric_file_explains_its_bar_rather_than_only_stating_it() -> None:
    """A number with no reasoning beside it cannot be argued with, only obeyed."""
    for path in sorted(_RUBRIC_ROOT.rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document.get("description", "").strip(), f"{path} states a bar with no description"
        assert document.get("criteria"), f"{path} names no criteria"
        assert document.get("direction") in {"maximize", "minimize"}, f"{path}: no direction"


def test_a_safety_metric_carries_the_strictest_bar_in_its_rubric() -> None:
    """Baseline practice E2, asserted against the rubrics rather than trusted.

    Every metric whose name ends in ``safety`` gates a leak, and a leak gate that is right most
    of the time is not a gate. The bar is at least 0.99 everywhere, and stricter where a single
    occurrence is unacceptable.
    """
    for rubric, bars in run_eval.THRESHOLDS.items():
        for metric, threshold in bars.items():
            if metric.endswith("safety"):
                assert threshold >= 0.99, f"{rubric}:{metric} gates a leak at {threshold}"


# ------------------------------------------------------------------ the loader fails closed
def test_a_missing_rubric_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no rubric directory"):
        run_eval.load_thresholds_from_rubrics(tmp_path / "absent")


def test_a_rubric_with_no_thresholds_gates_nothing_and_is_refused(tmp_path: Path) -> None:
    for rubric in run_eval.RUBRICS:
        (tmp_path / rubric).mkdir(parents=True)
    with pytest.raises(SystemExit, match="no metric has a threshold"):
        run_eval.load_thresholds_from_rubrics(tmp_path)


def test_a_non_numeric_threshold_is_refused_rather_than_coerced(tmp_path: Path) -> None:
    """ "1.0" is a string, and a bar that had to be coerced was not reviewed as a number."""
    for rubric in run_eval.RUBRICS:
        (tmp_path / rubric).mkdir(parents=True)
        (tmp_path / rubric / "m.yaml").write_text("metric: m\nthreshold: high\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="non-numeric threshold"):
        run_eval.load_thresholds_from_rubrics(tmp_path)


def test_one_metric_given_two_different_bars_is_refused(tmp_path: Path) -> None:
    """The whole point of one home: two files disagreeing must not resolve by sort order."""
    for rubric in run_eval.RUBRICS:
        (tmp_path / rubric).mkdir(parents=True)
        (tmp_path / rubric / "a.yaml").write_text("metric: m\nthreshold: 1.0\n", encoding="utf-8")
        (tmp_path / rubric / "b.yaml").write_text("metric: m\nthreshold: 0.5\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="One metric, one bar"):
        run_eval.load_thresholds_from_rubrics(tmp_path)


def test_a_rubric_naming_no_metric_is_refused(tmp_path: Path) -> None:
    for rubric in run_eval.RUBRICS:
        (tmp_path / rubric).mkdir(parents=True)
        (tmp_path / rubric / "m.yaml").write_text("threshold: 1.0\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="names no metric"):
        run_eval.load_thresholds_from_rubrics(tmp_path)


def test_companion_metrics_are_thresholds_too(tmp_path: Path) -> None:
    """They group metrics a reader should consider together; they are not decoration."""
    for rubric in run_eval.RUBRICS:
        (tmp_path / rubric).mkdir(parents=True)
        (tmp_path / rubric / "m.yaml").write_text(
            "metric: headline\nthreshold: 1.0\n"
            "companion_metrics:\n  sidekick:\n    threshold: 0.9\n",
            encoding="utf-8",
        )
    loaded = run_eval.load_thresholds_from_rubrics(tmp_path)
    assert loaded[run_eval.AGENT_ASSIST] == {"headline": 1.0, "sidekick": 0.9}
