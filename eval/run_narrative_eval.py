#!/usr/bin/env python3
"""The half a rule cannot score: is the reply any good, judged, against a floor.

Everything in ``run_eval.py`` is deterministic and binary. Was the turn allowed. Was the reply
grounded. Did it cite what it claimed. Did anything personal survive. Those are the questions
code can answer, and code answers them there.

They leave a gap. A reply can be allowed, grounded, correctly cited, free of personal data and
still be useless, or worse: it can answer a question nobody asked, promise a refund, or tell a
customer who has just said they cannot pay that there is nothing to be done. Deciding that is a
judgement, so it is judged, and the judge is held to the same standard as every other scorer
here: it must be shown able to fail before anything it certifies is believed.

Three properties make this a measurement rather than a vote:

* **The judge is OFFLINE by default and chosen on the command line.** ``--judge deterministic``
  is the default and needs no model, no credentials and no network, so this runs inside the
  gate. ``--judge local-model`` sends the narratives to an OpenAI-compatible endpoint the
  operator names. No environment variable can redirect it: a gate whose scorer a stray variable
  could swap is not a gate.
* **The bar is DATA, owned by model risk**, in ``config/quality-floors.toml``. A floor refuses;
  a target is full quality; between them is DEGRADED, which is the band the portability story
  described in words and nothing measured.
* **The expectation is a TABLE, not a threshold.** Each case carries the same reply written
  three ways with the band each should land in, so a profile that quietly got BETTER fails too.
  A band nobody predicted is a change nobody reviewed.

Exit is ``0`` only when every measured band equals its expectation AND the table is still
falsifiable: at least one expectation DEGRADED, at least one UNFIT, and the deliberate control
below the floor.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_eval_kit.floors import Fitness, QualityFloors, load_quality_floors
from agent_eval_kit.judge import (
    JudgeConfigError,
    JudgePort,
    JudgeRequest,
    JudgeSelection,
    JudgeUnavailableError,
    NarrativeCriterion,
    assert_judge_can_go_red,
    build_judge,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET = _REPO_ROOT / "eval" / "datasets" / "narrative_golden.jsonl"
FLOORS = _REPO_ROOT / "config" / "quality-floors.toml"

#: The three ways every case is written. The names are profiles rather than adjectives: they say
#: WHICH deployment produced the narrative, which is what a portability claim is about.
PROFILES = ("managed", "reduced", "regressed")

#: The control. It exists to be below the floor, so a table where it passes is a table whose
#: floor is too low to refuse anything.
CONTROL = "regressed"


class NarrativeEvalError(RuntimeError):
    """The table is unusable, or the run cannot be trusted to have measured anything."""


@dataclass(frozen=True, slots=True)
class Measured:
    """One narrative, graded, and the band it landed in against its vertical's floor."""

    case_id: str
    vertical: str
    profile: str
    score: float
    fitness: Fitness
    expected: Fitness

    @property
    def matched(self) -> bool:
        return self.fitness is self.expected


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    if not cases:
        raise NarrativeEvalError(f"{path}: the degradation table is empty, so nothing is judged")
    return cases


def check_table_is_falsifiable(cases: list[dict[str, Any]]) -> None:
    """A table of passes proves nothing. Refuse one before it is run, not after.

    Three structural rules, each closing a way this could look like evidence while being none:
    the control must be expected UNFIT, some expectation must be DEGRADED, and some must be
    UNFIT. A table where everything is expected FIT would certify any judge that returns high
    numbers, including one that returns the same high number for every input.
    """
    expectations = {Fitness(str(band)) for case in cases for band in case["expected"].values()}
    if Fitness.DEGRADED not in expectations:
        raise NarrativeEvalError(
            "no case expects DEGRADED, so the middle band is unmeasured and the floor and the "
            "target are indistinguishable"
        )
    if Fitness.UNFIT not in expectations:
        raise NarrativeEvalError(
            "no case expects UNFIT, so nothing in this table would refuse a profile"
        )
    for case in cases:
        band = Fitness(str(case["expected"][CONTROL]))
        if band is not Fitness.UNFIT:
            raise NarrativeEvalError(
                f"{case['id']}: the {CONTROL!r} control is expected {band.value!r}. It is the "
                "deliberate defect; a table where it passes has a floor that refuses nothing."
            )


def criteria_for(case: dict[str, Any]) -> tuple[NarrativeCriterion, ...]:
    return tuple(NarrativeCriterion.from_mapping(row) for row in case["criteria"])


def measure(cases: list[dict[str, Any]], judge: JudgePort, floors: QualityFloors) -> list[Measured]:
    """Grade every candidate and place it in a band. A judge that grades nothing is an error."""
    measured: list[Measured] = []
    for case in cases:
        criteria = criteria_for(case)
        floor = floors.floor_for(case["vertical"])
        for profile in PROFILES:
            candidate = case["candidates"][profile]
            verdict = judge.grade(
                JudgeRequest(
                    candidate=candidate,
                    criteria=criteria,
                    subject=case.get("subject", ""),
                )
            )
            if not verdict.has_evidence:
                raise JudgeUnavailableError(
                    f"{case['id']}/{profile}: the judge returned no graded criteria. A verdict "
                    "that measured nothing is not a low score, it is an absent measurement."
                )
            score = verdict.score
            if score >= floor.target:
                fitness = Fitness.FIT
            elif score >= floor.floor:
                fitness = Fitness.DEGRADED
            else:
                fitness = Fitness.UNFIT
            measured.append(
                Measured(
                    case_id=case["id"],
                    vertical=case["vertical"],
                    profile=profile,
                    score=round(score, 4),
                    fitness=fitness,
                    expected=Fitness(str(case["expected"][profile])),
                )
            )
    if not measured:
        raise NarrativeEvalError("nothing was measured, which is not a pass")
    return measured


def check_judge_can_go_red(
    cases: list[dict[str, Any]], judge: JudgePort, floors: QualityFloors
) -> None:
    """Turn falsification on the JUDGE, which is the one scorer whose failure is invisible.

    A broken metric returns a wrong number and something notices. A broken judge keeps returning
    numbers, they keep clearing the bar, and the certification it produces is indistinguishable
    from a real one. So before any verdict here is believed, the judge is shown to score a good
    narrative above the floor and the deliberate control below it, per case.
    """
    for case in cases:
        floor = floors.floor_for(case["vertical"])
        assert_judge_can_go_red(
            judge,
            criteria=criteria_for(case),
            green=case["candidates"]["managed"],
            red=case["candidates"][CONTROL],
            floor=floor.floor,
            metric=f"narrative_quality[{case['id']}]",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Narrative quality against model-risk floors, judged.",
    )
    parser.add_argument(
        "--judge",
        choices=("deterministic", "local-model"),
        default="deterministic",
        help=(
            "deterministic (default): offline, no model, runs in the gate. local-model: an "
            "OpenAI-compatible endpoint you name below. Chosen HERE and never from the "
            "environment, so no stray variable can swap the gate's scorer."
        ),
    )
    parser.add_argument("--judge-base-url", default="", help="Required by --judge local-model.")
    parser.add_argument("--judge-model", default="", help="Required by --judge local-model.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--floors", type=Path, default=FLOORS)
    return parser


def selection_from(args: argparse.Namespace) -> JudgeSelection:
    if args.judge == "deterministic":
        return JudgeSelection(backend="deterministic")
    if not args.judge_base_url.strip() or not args.judge_model.strip():
        raise JudgeConfigError(
            "--judge local-model needs BOTH --judge-base-url and --judge-model. A model judge "
            "half-configured would fall back to something, and what it fell back to would be "
            "the thing certifying the release."
        )
    return JudgeSelection(
        backend="local-model",
        base_url=args.judge_base_url.strip(),
        model=args.judge_model.strip(),
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cases = load_cases(args.dataset)
    check_table_is_falsifiable(cases)
    floors = load_quality_floors(args.floors)
    selection = selection_from(args)
    judge = build_judge(selection)

    check_judge_can_go_red(cases, judge, floors)
    measured = measure(cases, judge, floors)

    name = selection.backend
    print("")
    print(f"=== narrative quality (judge: {name}) ===")
    print(f"  floors  : {args.floors}")
    print(f"  cases   : {len(cases)}  narratives: {len(measured)}")
    print("")
    print("  case                   profile     score   band       expected   result")
    print("  " + "-" * 74)
    for row in measured:
        verdict = "PASS" if row.matched else "FAIL"
        print(
            f"  {row.case_id:<22} {row.profile:<11} {row.score:5.3f}   "
            f"{row.fitness.value:<10} {row.expected.value:<10} {verdict}"
        )
    failures = [row for row in measured if not row.matched]
    print("")
    print(f"  NARRATIVE GATE: {'PASS' if not failures else 'FAIL'}")
    if failures:
        for row in failures:
            print(
                f"    {row.case_id}/{row.profile}: measured {row.fitness.value!r}, "
                f"the table says {row.expected.value!r}"
            )
        print(
            "    A band that moved is a quality change nobody reviewed. Recalibrate the table "
            "in the same commit as whatever moved it, or fix what moved."
        )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
