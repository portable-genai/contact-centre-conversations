"""Falsification, turned on the JUDGE: the one scorer whose failure is invisible.

A broken deterministic metric returns a wrong number and something notices. A broken judge keeps
returning numbers, they keep clearing the bar, and the certification it produces is
indistinguishable from a real one. So the judge is held to the standard everything else here is
held to, and then two specific ways it can be broken are constructed and caught:

* a judge that certifies ANYTHING, which is what a model with a badly worded prompt looks like;
* a judge that returns a verdict with nothing graded, which must RAISE rather than score zero,
  because "we could not measure" and "it measured badly" are different facts.

Plus the two structural properties that stop the table itself being decorative: it must contain
a band that refuses, and no environment variable may swap the gate's scorer for a networked one.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import run_narrative_eval as narrative
from agent_eval_kit.floors import Fitness, load_quality_floors
from agent_eval_kit.harness import NotFalselyGreenError
from agent_eval_kit.judge import (
    CriterionScore,
    JudgeConfigError,
    JudgeRequest,
    JudgeUnavailableError,
    JudgeVerdict,
    NarrativeCriterion,
    assert_judge_can_go_red,
    build_judge,
)

from tests import REPO_ROOT

_FLOORS = REPO_ROOT / "config" / "quality-floors.toml"


class _AlwaysHappyJudge:
    """Certifies anything. What a model judge with a badly worded prompt actually looks like."""

    def grade(self, request: JudgeRequest) -> JudgeVerdict:
        return JudgeVerdict(
            graded_by="always-happy",
            scores=tuple(
                CriterionScore(criterion=c.name, score=1.0, weight=c.weight)
                for c in request.criteria
            ),
        )


class _SilentJudge:
    """Answers, and grades nothing. An absent measurement wearing a verdict's clothes."""

    def grade(self, request: JudgeRequest) -> JudgeVerdict:
        return JudgeVerdict(graded_by="silent", scores=())


# ------------------------------------------------------------------ the floors are real
def test_the_floors_document_names_both_promotion_bundles() -> None:
    floors = load_quality_floors(_FLOORS)
    assert set(floors.verticals) == {
        "contact-centre-conversations-agent-assist",
        "contact-centre-conversations-self-service",
    }


def test_the_customer_facing_bar_is_the_higher_one() -> None:
    """A trained agent can discard a weak suggestion. Nobody reviews the self-service reply."""
    floors = load_quality_floors(_FLOORS)
    agent = floors.floor_for("contact-centre-conversations-agent-assist")
    customer = floors.floor_for("contact-centre-conversations-self-service")
    assert customer.floor > agent.floor
    assert customer.target > agent.target


def test_every_floor_leaves_a_degraded_band_between_the_bars() -> None:
    """Floor equal to target collapses DEGRADED, and degradation is the thing being measured."""
    for floor in load_quality_floors(_FLOORS):
        assert floor.target > floor.floor, floor.vertical


def test_a_vertical_with_no_floor_raises_rather_than_taking_a_default() -> None:
    floors = load_quality_floors(_FLOORS)
    with pytest.raises(Exception, match="no quality floor"):
        floors.floor_for("contact-centre-conversations-voice")


# ------------------------------------------------------------------ the judge can fail
def test_the_offline_judge_can_go_red_on_every_case() -> None:
    """The standing proof. Run before any verdict in this file is believed."""
    cases = narrative.load_cases()
    floors = load_quality_floors(_FLOORS)
    narrative.check_judge_can_go_red(cases, build_judge(), floors)


def test_a_judge_that_certifies_anything_is_caught() -> None:
    cases = narrative.load_cases()
    floors = load_quality_floors(_FLOORS)
    with pytest.raises(NotFalselyGreenError):
        narrative.check_judge_can_go_red(cases, _AlwaysHappyJudge(), floors)


def test_a_judge_that_grades_nothing_raises_rather_than_scoring_zero() -> None:
    """Scoring it zero would read as "the narrative was bad" when nothing was measured at all."""
    cases = narrative.load_cases()
    floors = load_quality_floors(_FLOORS)
    with pytest.raises(JudgeUnavailableError):
        narrative.measure(cases, _SilentJudge(), floors)


def test_the_helper_itself_catches_a_judge_that_cannot_distinguish() -> None:
    criterion = NarrativeCriterion(name="c", must_cover=["recorded"])
    with pytest.raises(NotFalselyGreenError):
        assert_judge_can_go_red(
            _AlwaysHappyJudge(),
            criteria=[criterion],
            green="the call is recorded",
            red="nothing at all",
            floor=0.8,
        )


# ------------------------------------------------------------------ the table is falsifiable
def test_the_shipped_table_measures_a_band_that_refuses() -> None:
    narrative.check_table_is_falsifiable(narrative.load_cases())


def test_a_table_where_everything_passes_is_refused() -> None:
    """It would certify any judge that returns high numbers, including a constant one."""
    cases = narrative.load_cases()
    for case in cases:
        case["expected"] = dict.fromkeys(case["expected"], Fitness.FIT.value)
    with pytest.raises(narrative.NarrativeEvalError, match="no case expects DEGRADED"):
        narrative.check_table_is_falsifiable(cases)


def test_a_control_that_is_expected_to_pass_is_refused() -> None:
    cases = narrative.load_cases()
    cases[0]["expected"][narrative.CONTROL] = Fitness.FIT.value
    with pytest.raises(narrative.NarrativeEvalError, match="control is expected"):
        narrative.check_table_is_falsifiable(cases)


def test_an_empty_table_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("# every case was removed\n", encoding="utf-8")
    with pytest.raises(narrative.NarrativeEvalError, match="degradation table is empty"):
        narrative.load_cases(empty)


def test_the_measured_bands_match_the_table() -> None:
    """The run itself, as a test, so a band moving fails the build and not only the command."""
    assert narrative.main([]) == 0


# ------------------------------------------------------------------ the gate stays offline
def test_the_default_judge_needs_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proved by removing the socket, not by reading the code."""

    def _no_sockets(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the offline judge opened a socket")

    monkeypatch.setattr(socket, "socket", _no_sockets)
    monkeypatch.setattr(socket, "create_connection", _no_sockets)
    assert narrative.main([]) == 0


@pytest.mark.parametrize(
    "variables",
    [
        {"AGENT_EVAL_JUDGE": "local-model"},
        {"AGENT_EVAL_JUDGE_BASE_URL": "http://elsewhere.example"},
        {"AGENT_EVAL_JUDGE": "local-model", "AGENT_EVAL_JUDGE_MODEL": "something"},
    ],
)
def test_no_environment_variable_can_swap_the_gates_judge(
    monkeypatch: pytest.MonkeyPatch, variables: dict[str, str]
) -> None:
    """A gate whose scorer a stray variable could redirect is not a gate.

    The commons offers `JudgeSelection.from_env`, and this runner deliberately does not use it:
    the judge is named on the command line, so the thing certifying a release is chosen by
    whoever ran it rather than by whatever the shell happened to carry.
    """
    for name, value in variables.items():
        monkeypatch.setenv(name, value)
    assert narrative.selection_from(narrative.build_parser().parse_args([])).is_offline


def test_a_half_configured_model_judge_refuses_rather_than_falling_back() -> None:
    """What it fell back to would be the thing certifying the release."""
    args = narrative.build_parser().parse_args(["--judge", "local-model"])
    with pytest.raises(JudgeConfigError, match="BOTH"):
        narrative.selection_from(args)


def test_a_named_model_judge_is_selectable_so_the_option_is_real() -> None:
    args = narrative.build_parser().parse_args(
        [
            "--judge",
            "local-model",
            "--judge-base-url",
            "http://localhost:1234",
            "--judge-model",
            "m",
        ]
    )
    selection = narrative.selection_from(args)
    assert not selection.is_offline
    assert selection.model == "m"
