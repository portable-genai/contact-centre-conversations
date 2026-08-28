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

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import run_eval
import yaml  # the eval harness, importable because eval/ is on the path below
from agent_eval_kit import assert_each_can_go_red

from contact_centre_conversations.config import (
    Settings,
    load_packs,
)
from contact_centre_conversations.domain import action_engine, suggestions
from contact_centre_conversations.domain.contact_kernel import ContactKernel
from contact_centre_conversations.domain.models import AUDIENCE_INTERNAL, AUDIENCE_PUBLIC
from contact_centre_conversations.domain.modes import ContactMode
from contact_centre_conversations.domain.packs import (
    PackLibrary,
)

from tests import REPO_ROOT

_AA = run_eval.AGENT_ASSIST
_SS = run_eval.SELF_SERVICE


#: Every mutation below edits the SHIPPED scenario documents and writes the result to a
#: temporary tree, so a red input is the real corpus with one specific defect introduced rather
#: than a hand-built file that might differ in some other way and pass for the wrong reason.
def _mutated(tmp_path: Path, name: str, mutate: Any) -> Path:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(sorted(run_eval.SCENARIOS.rglob("*.yaml"))):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(document)
        (root / f"{index:02d}-{path.name}").write_text(
            yaml.safe_dump(document, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    return root


def _scenarios(document: dict[str, Any], mode: str) -> list[dict[str, Any]]:
    """The scenarios in one document, when it belongs to ``mode``, else nothing."""
    return document["scenarios"] if document.get("mode") == mode else []


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

    def _wrong_state(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _AA):
            case["expected_state"] = (
                "greeting" if case["expected_state"] != "greeting" else "block_card"
            )

    def _wrong_timing(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _AA):
            case["expected_missed"] = ["data_use_notice"]
            case["expected_due"] = ["data_use_notice"]

    red_next_step = _mutated(tmp_path, "aa-next-step", _wrong_state)
    red_timing = _mutated(tmp_path, "aa-timing", _wrong_timing)

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

    def _wrong_cite(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _AA):
            if case.get("expected_citations"):
                case["expected_citations"] = ["kb-sg-001"]

    def _wrong_ground(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _AA):
            if case.get("expected_grounded_facts"):
                case["expected_grounded_facts"] = ["The replacement arrives within 5 working days"]

    red_cite = _mutated(tmp_path, "aa-cite", _wrong_cite)
    red_ground = _mutated(tmp_path, "aa-ground", _wrong_ground)

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

    def _leak(document: dict[str, Any]) -> None:
        # The audit summary carries the contact id verbatim (a reference, not personal data), so
        # a case whose contact id IS the planted token proves the scorer notices a real leak.
        for case in _scenarios(document, _AA):
            token = f"{case['contact_id']}-S1234567D"
            case["contact_id"] = token
            case["planted"] = token
            case["turns"][0]["text"] += f" {token}"

    red = _mutated(tmp_path, "aa-pii", _leak)
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

    def _mislabel(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _SS):
            for turn in case["turns"]:
                turn["expected_handoff"] = "vulnerability" if not turn["expected_handoff"] else ""

    red = _mutated(tmp_path, "ss-handoff", _mislabel)
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

    def _all_allow(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _SS):
            for turn in case["turns"]:
                turn["expected_outcome"] = "allow"

    red = _mutated(tmp_path, "ss-broken", _all_allow)
    assert run_eval.main(["--rubric", _SS, "--dataset", str(red)]) == 1


def test_the_dataset_override_needs_a_single_rubric() -> None:
    assert run_eval.main(["--dataset", str(run_eval.DATASETS[_SS])]) == 2


@pytest.mark.parametrize("rubric", list(run_eval.RUBRICS))
def test_each_shipped_dataset_exists_and_carries_labels(rubric: str) -> None:
    rows = run_eval.load_scenarios(run_eval.DATASETS[rubric], rubric)
    assert rows, f"{rubric}: an empty scenario set scores a vacuous 1.0"
    assert all(row.get("id") for row in rows)
    assert (REPO_ROOT / "eval" / "scenarios").is_dir()


# --------------------------------------------------------------------------------------- #
# The compliance metrics, each against the specific defect it exists to catch
# --------------------------------------------------------------------------------------- #
def _score_once(rubric: str, metric: str) -> float:
    report = run_eval.SMOKE[rubric](run_eval.DATASETS[rubric])
    return next(result.score for result in report.results if result.metric == metric)


def test_party_isolation_goes_red_when_the_ownership_check_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deliberate defect is in the PRODUCT, not in the data, and that is the whole point.

    Widening the ownership fixture does not work as a red input, and the harness said so the
    first time this was written that way: giving every record to every party removes the
    VIOLATION rather than creating one, so the metric stayed at 1.0 and proved nothing. The
    defect this metric exists to catch is the check going away, so that is what is removed here:
    with `unowned_parameters` returning nothing, a well-formed parameter is permission again and
    the read executes against another customer's card.

    Written as an explicit before-and-after rather than through `assert_each_can_go_red`, whose
    payload shape carries datasets rather than product mutations.
    """
    metric = "customer_party_isolation_safety"
    green = _score_once(_SS, metric)
    assert green >= run_eval.THRESHOLDS[_SS][metric], "the clean corpus should pass"

    monkeypatch.setattr(action_engine, "unowned_parameters", lambda *_args, **_kw: ())
    red = _score_once(_SS, metric)
    assert red < run_eval.THRESHOLDS[_SS][metric], (
        f"{metric} scored {red} with the ownership check removed, so it cannot go red"
    )


def test_citation_audience_goes_red_when_the_customer_facing_filter_is_widened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same shape: the defect is the control, not the corpus.

    Reclassifying passages does not work either, and for a subtler reason the harness caught:
    the metric reads the SAME corpus the product filtered on, so flipping a row to public makes
    both agree and the metric stays green. What must go red is self-service being allowed to
    ground a reply in staff-only wording, so that is what is widened.
    """
    metric = "customer_citation_audience_safety"
    green = _score_once(_SS, metric)
    assert green >= run_eval.THRESHOLDS[_SS][metric], "the clean corpus should pass"

    monkeypatch.setitem(
        suggestions.AUDIENCES_FOR_MODE,
        ContactMode.SELF_SERVICE,
        frozenset({AUDIENCE_PUBLIC, AUDIENCE_INTERNAL}),
    )
    red = _score_once(_SS, metric)
    assert red < run_eval.THRESHOLDS[_SS][metric], (
        f"{metric} scored {red} with the customer-facing audience filter widened"
    )


def test_escalation_recall_goes_red_when_a_contact_that_needs_a_human_gets_none(
    tmp_path: Path,
) -> None:
    """The red input labels benign contacts as needing a human, and they do not get one.

    Scored against the reviewer's own `expected_escalation`, so widening that label is the same
    shape as the product silently narrowing what it escalates: in both, a contact somebody said
    needs a person does not reach one.
    """
    green = run_eval.DATASETS[_SS]

    def _everything_needs_review(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _SS):
            case["expected_escalation"] = True

    red = _mutated(tmp_path, "ss-escalation", _everything_needs_review)
    assert_each_can_go_red(
        _score(_SS, "escalation_recall"),
        {"self_service": ((green, None), (red, None))},
        threshold=run_eval.THRESHOLDS[_SS]["escalation_recall"],
        metric="escalation_recall",
    )


def test_injection_handling_goes_red_when_an_attack_is_labelled_allowed(tmp_path: Path) -> None:
    green = run_eval.DATASETS[_SS]

    def _attacks_are_fine(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _SS):
            if case["family"].startswith("injection_") or case["family"] == "handoff_jailbreak":
                for turn in case["turns"]:
                    turn["expected_outcome"] = "allow"

    red = _mutated(tmp_path, "ss-injection", _attacks_are_fine)
    assert_each_can_go_red(
        _score(_SS, "injection_handling_safety"),
        {"self_service": ((green, None), (red, None))},
        threshold=run_eval.THRESHOLDS[_SS]["injection_handling_safety"],
        metric="injection_handling_safety",
    )


def test_customer_pii_safety_goes_red_when_a_planted_token_survives(tmp_path: Path) -> None:
    """Same trick as the agent-assist proof, on the mode that reaches the public.

    The audit summary carries the contact id verbatim, so a contact id that IS the planted token
    is a real leak the scorer must see rather than a synthetic one it could be taught to ignore.
    """
    green = run_eval.DATASETS[_SS]

    def _leak(document: dict[str, Any]) -> None:
        for case in _scenarios(document, _SS):
            # Unique per scenario: one shared id across tenants is a store conflict, not a leak.
            token = f"{case['contact_id']}-S1234567D"
            case["contact_id"] = token
            case["planted"] = token
            case["turns"][0]["text"] += f" {token}"

    red = _mutated(tmp_path, "ss-pii", _leak)
    assert_each_can_go_red(
        _score(_SS, "customer_pii_safety"),
        {"self_service": ((green, None), (red, None))},
        threshold=run_eval.THRESHOLDS[_SS]["customer_pii_safety"],
        metric="customer_pii_safety",
    )


@pytest.mark.parametrize(
    ("rubric", "metric"),
    [(_AA, "audit_completeness"), (_SS, "review_routing_safety")],
)
def test_the_audit_metrics_go_red_when_a_turn_leaves_no_record(
    monkeypatch: pytest.MonkeyPatch, rubric: str, metric: str
) -> None:
    """A turn that wrote no audit record is a decision nobody can review afterwards.

    The deliberate defect is a FORGETFUL writer: every second record is dropped, so the count
    stops matching the scenarios' own turn count. Both modes are proved, because the
    customer-facing one is where a missing record means nobody at all noticed.
    """
    green = _score_once(rubric, metric)
    assert green >= run_eval.THRESHOLDS[rubric][metric], "the clean corpus should pass"

    original = ContactKernel.record
    calls = {"n": 0}

    def _forgetful(self: ContactKernel, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] % 2:
            original(self, **kwargs)

    monkeypatch.setattr(ContactKernel, "record", _forgetful)
    red = _score_once(rubric, metric)
    assert red < run_eval.THRESHOLDS[rubric][metric], (
        f"{metric} scored {red} while half the audit records were dropped"
    )
