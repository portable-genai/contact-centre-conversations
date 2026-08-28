"""A metric that cannot go RED is not a metric: prove each one, per rubric, per mode.

``agent_eval_kit.assert_each_can_go_red`` runs the REAL scorer over a green input and a red one
and fails unless the score crosses the threshold between them. The red inputs here are not
random noise: each is the specific defect the metric exists to catch, so a metric that stopped
detecting its own defect class fails this suite rather than staying quietly green.

The gate-precision falsification is the one the plan names explicitly, and it is done the way it
has to be done: by injecting a WILDCARD intent into a test allowlist. A gate that admits
everything scores 0 against a golden set containing adversarial out-of-scope asks, so the metric
goes red exactly when the fail-closed property is removed. Nothing in the shipped packs can
express that wildcard; it is constructible only here, in a test.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import run_eval  # the eval harness, importable because eval/ is on the path below
from agent_eval_kit import assert_each_can_go_red

from contact_centre_conversations.config import (
    Settings,
    load_packs,
)
from contact_centre_conversations.domain.packs import (
    PackLibrary,
)

from tests import REPO_ROOT

_AA = run_eval.AGENT_ASSIST
_SS = run_eval.SELF_SERVICE


def _rows(rubric: str) -> list[dict[str, Any]]:
    return run_eval.load_cases(run_eval.DATASETS[rubric])


def _written(tmp_path: Path, rows: list[dict[str, Any]], name: str) -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _score(rubric: str, metric: str) -> Any:
    runner = run_eval.SMOKE[rubric]

    def _run(payload: tuple[Path, Settings | None]) -> float:
        dataset, settings = payload
        report = runner(dataset) if settings is None else runner(dataset, settings)  # type: ignore[call-arg]
        return next(result.score for result in report.results if result.metric == metric)

    return _run


# --------------------------------------------------------------------------------------- #
# Agent assist
# --------------------------------------------------------------------------------------- #
def test_the_agent_assist_metrics_can_each_go_red(tmp_path: Path) -> None:
    green = run_eval.DATASETS[_AA]

    wrong_state = _rows(_AA)
    for row in wrong_state:
        row["expected_state"] = "greeting" if row["expected_state"] != "greeting" else "block_card"
    red_next_step = _written(tmp_path, wrong_state, "aa-next-step.jsonl")

    wrong_timing = _rows(_AA)
    for row in wrong_timing:
        row["expected_missed"] = ["data_use_notice"]
        row["expected_due"] = ["data_use_notice"]
    red_timing = _written(tmp_path, wrong_timing, "aa-timing.jsonl")

    assert_each_can_go_red(
        _score(_AA, "next_step_accuracy"),
        {"agent_assist": ((green, None), (red_next_step, None))},
        threshold=run_eval.THRESHOLDS[_AA]["next_step_accuracy"],
        metric="next_step_accuracy",
    )
    assert_each_can_go_red(
        _score(_AA, "reminder_timeliness"),
        {"agent_assist": ((green, None), (red_timing, None))},
        threshold=run_eval.THRESHOLDS[_AA]["reminder_timeliness"],
        metric="reminder_timeliness",
    )


def test_the_suggestion_metrics_can_each_go_red(tmp_path: Path) -> None:
    """citation_accuracy and groundedness must fail when their golden label is corrupted.

    Both are scored against the dataset's own oracle, so the falsification is the plan's shape:
    corrupt the label and the metric goes red. citation_accuracy red points the expected
    provenance at a real-but-WRONG passage (the exact defect the old presence check could never
    catch); groundedness red relabels the expected fact with a real KB fact the reply does not
    assert, so a reply grounded in itself no longer passes.
    """
    green = run_eval.DATASETS[_AA]

    wrong_cite = _rows(_AA)
    for row in wrong_cite:
        if row.get("expected_citations"):
            row["expected_citations"] = ["kb-sg-001"]
    red_cite = _written(tmp_path, wrong_cite, "aa-cite.jsonl")

    wrong_ground = _rows(_AA)
    for row in wrong_ground:
        if row.get("expected_grounded_facts"):
            row["expected_grounded_facts"] = ["The replacement arrives within 5 working days"]
    red_ground = _written(tmp_path, wrong_ground, "aa-ground.jsonl")

    assert_each_can_go_red(
        _score(_AA, "citation_accuracy"),
        {"agent_assist": ((green, None), (red_cite, None))},
        threshold=run_eval.THRESHOLDS[_AA]["citation_accuracy"],
        metric="citation_accuracy",
    )
    assert_each_can_go_red(
        _score(_AA, "groundedness"),
        {"agent_assist": ((green, None), (red_ground, None))},
        threshold=run_eval.THRESHOLDS[_AA]["groundedness"],
        metric="groundedness",
    )


def test_the_pii_metric_goes_red_when_redaction_is_removed(tmp_path: Path) -> None:
    """The red case plants an identifier the pattern pack does NOT cover, so the mask misses it."""
    green = run_eval.DATASETS[_AA]
    leaky = _rows(_AA)
    # The audit summary carries the contact id verbatim (it is a reference, not personal data),
    # so a case whose contact id IS the planted token proves the scorer notices a real leak.
    leaky[0]["contact_id"] = "eval-aa-leak-S1234567D"
    leaky[0]["planted"] = "eval-aa-leak-S1234567D"
    red = _written(tmp_path, leaky, "aa-pii.jsonl")
    assert_each_can_go_red(
        _score(_AA, "pii_safety"),
        {"agent_assist": ((green, None), (red, None))},
        threshold=run_eval.THRESHOLDS[_AA]["pii_safety"],
        metric="pii_safety",
    )


# --------------------------------------------------------------------------------------- #
# Self service: the wildcard allowlist
# --------------------------------------------------------------------------------------- #
def _wildcard_settings() -> Settings:
    """An allowlist that admits everything, which the shipped pack schema cannot express.

    The wildcard is a phrase that matches every utterance, plus every action reachable from it.
    Building it here rather than in ``config/packs/`` is the point: this is the mutant, and it
    must not be something a deployment could accidentally ship.
    """
    wildcard = PackLibrary.from_documents(
        [
            {
                "kind": "actions",
                "catalog_id": "retail-actions-v1",
                "vertical": "retail_banking",
                "actions": [
                    {
                        "action_id": "read_card_balance",
                        "title": "Read the card balance",
                        "consequential": False,
                        "severity": "low",
                        "parameters": [
                            {"name": "card_last4", "pattern": "[0-9]{4}", "binds_to_party": True}
                        ],
                    },
                    {
                        "action_id": "read_recent_transactions",
                        "title": "List transactions",
                        "consequential": False,
                        "severity": "low",
                        "parameters": [
                            {"name": "card_last4", "pattern": "[0-9]{4}", "binds_to_party": True}
                        ],
                    },
                    {
                        "action_id": "block_card",
                        "title": "Block the card",
                        "consequential": False,
                        "severity": "high",
                        "parameters": [
                            {"name": "card_last4", "pattern": "[0-9]{4}", "binds_to_party": True}
                        ],
                    },
                    {
                        "action_id": "raise_chargeback",
                        "title": "Raise a chargeback",
                        "consequential": False,
                        "severity": "critical",
                        "parameters": [
                            {"name": "card_last4", "pattern": "[0-9]{4}", "binds_to_party": True},
                            {
                                "name": "transaction_ref",
                                "pattern": "TXN-[0-9]{6}",
                                "binds_to_party": True,
                            },
                        ],
                    },
                ],
            },
            {
                "kind": "allowlist",
                "tenant": "demo-bank",
                "market": "SG",
                "vertical": "retail_banking",
                "locale": "en-SG",
                "confidence_floor": 0.0,
                "intents": [
                    {
                        "intent_id": "anything",
                        "title": "The wildcard: matches every utterance",
                        "confidence_floor": 0.0,
                        # A single letter every English sentence in the golden set contains.
                        "phrases": ["e"],
                        "actions": [
                            "read_card_balance",
                            "read_recent_transactions",
                            "block_card",
                            "raise_chargeback",
                        ],
                    }
                ],
                "actions": [
                    "read_card_balance",
                    "read_recent_transactions",
                    "block_card",
                    "raise_chargeback",
                ],
            },
        ]
    )
    # Only the allowlist and the catalog are mutated: the procedure, disclosure and cue packs
    # stay the shipped ones, so the mutant differs from the real deployment in exactly the
    # property under test and in nothing else.
    shipped = load_packs(REPO_ROOT / "config" / "packs")
    packs = replace(shipped, allowlists=wildcard.allowlists, catalogs=wildcard.catalogs)
    return run_eval.eval_settings(packs=packs)


def test_gate_precision_goes_red_when_a_wildcard_is_injected() -> None:
    """The proof the plan asks for: gate precision must be 1.0 and must be able to fail."""
    dataset = run_eval.DATASETS[_SS]
    assert_each_can_go_red(
        _score(_SS, "gate_precision"),
        {"self_service": ((dataset, None), (dataset, _wildcard_settings()))},
        threshold=run_eval.THRESHOLDS[_SS]["gate_precision"],
        metric="gate_precision",
    )


def test_maker_checker_safety_goes_red_when_actions_stop_being_consequential() -> None:
    """The wildcard catalog also marks every action non-consequential, so they execute."""
    dataset = run_eval.DATASETS[_SS]
    assert_each_can_go_red(
        _score(_SS, "maker_checker_safety"),
        {"self_service": ((dataset, None), (dataset, _wildcard_settings()))},
        threshold=run_eval.THRESHOLDS[_SS]["maker_checker_safety"],
        metric="maker_checker_safety",
    )


def test_handoff_safety_goes_red_when_the_expected_triggers_are_wrong(tmp_path: Path) -> None:
    dataset = run_eval.DATASETS[_SS]
    mislabelled = _rows(_SS)
    for row in mislabelled:
        row["expected_handoff"] = "vulnerability" if not row["expected_handoff"] else ""
    red = _written(tmp_path, mislabelled, "ss-handoff.jsonl")
    assert_each_can_go_red(
        _score(_SS, "handoff_safety"),
        {"self_service": ((dataset, None), (red, None))},
        threshold=run_eval.THRESHOLDS[_SS]["handoff_safety"],
        metric="handoff_safety",
    )


# --------------------------------------------------------------------------------------- #
# The two rubrics stay separate
# --------------------------------------------------------------------------------------- #
def test_the_two_rubrics_report_separately_and_share_no_metric_name() -> None:
    """Each Hrz4 promotion gate consumes only its own, so a shared name would blur them."""
    aa = {result.metric for result in run_eval.run_agent_assist(run_eval.DATASETS[_AA]).results}
    ss = {result.metric for result in run_eval.run_self_service(run_eval.DATASETS[_SS]).results}
    assert aa and ss
    assert aa.isdisjoint(ss), f"the two rubric sets share metric names: {sorted(aa & ss)}"
    assert run_eval.BUNDLES[_AA] != run_eval.BUNDLES[_SS]


def test_the_gate_runs_both_rubrics_and_fails_if_either_fails(tmp_path: Path) -> None:
    assert run_eval.main([]) == 0
    broken = _rows(_SS)
    for row in broken:
        row["expected_outcome"] = "allow"
    red = _written(tmp_path, broken, "ss-broken.jsonl")
    assert run_eval.main(["--rubric", _SS, "--dataset", str(red)]) == 1


def test_the_dataset_override_needs_a_single_rubric() -> None:
    assert run_eval.main(["--dataset", str(run_eval.DATASETS[_SS])]) == 2


@pytest.mark.parametrize("rubric", list(run_eval.RUBRICS))
def test_each_shipped_dataset_exists_and_carries_labels(rubric: str) -> None:
    rows = _rows(rubric)
    assert rows, f"{rubric}: an empty golden set scores a vacuous 1.0"
    assert all(row.get("id") for row in rows)
    assert (REPO_ROOT / "eval" / "datasets").is_dir()
