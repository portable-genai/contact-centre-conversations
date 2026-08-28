"""Scenarios as YAML a reviewer can read and edit, validated hard on the way in.

The fleet authors golden sets as JSONL. That is a fine machine format and a poor review surface:
one case per line, no comments, no room to say WHY a case expects what it expects. The people who
decide what a contact centre may say are compliance and conduct reviewers, not the people who
wrote the runner, so the cases they own are YAML here, grouped by vertical, market and family,
with prose at the top of every file explaining what that family covers.

The rule the format cannot loosen: **every expected label is written by hand from the packs**,
never read back from a run. A metric scored against the pipeline's own verdict is a tautology
with a threshold. The loader enforces the shape; only a reviewer can enforce the substance, and
the file comments are addressed to them.

Validation is deliberately strict and deliberately loud, naming the file and the scenario id,
because a scenario that fails to say what it expects is worse than one that is absent: it counts
towards a denominator while asserting nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "AGENT_ASSIST_FAMILIES",
    "SELF_SERVICE_FAMILIES",
    "ScenarioError",
    "load_scenarios",
]

#: The market and locale pairs this service ships packs for. A scenario naming anything else
#: would be scored against packs that do not exist, which surfaces as a runtime error deep in a
#: service rather than as a dataset problem.
_MARKET_LOCALE = {"SG": "en-SG", "JP": "ja-JP"}
_VERTICALS = ("retail_banking", "general_insurance")
_TENANTS = ("demo-bank", "demo-insurer", "rival-bank")

#: Self-service scenario families. `family` is what the report groups by, so it is a closed set:
#: a typo would silently create a group of one that nobody notices is missing from the others.
SELF_SERVICE_FAMILIES = (
    "benign",
    "high_stakes",
    "out_of_scope",
    "cross_party",
    "cross_tenant",
    "vulnerability",
    "injection_direct",
    "injection_obfuscated",
    "injection_multilingual",
    "handoff_jailbreak",
    "repeated_failure",
)

#: Families whose scenarios are adversarial by nature and are therefore excluded from
#: containment: containment measures how much ordinary demand resolves without a person, and
#: counting a refused attack as a containment failure would reward answering it.
_ADVERSARIAL = frozenset(
    {
        "out_of_scope",
        "cross_party",
        "cross_tenant",
        "injection_direct",
        "injection_obfuscated",
        "injection_multilingual",
        "handoff_jailbreak",
    }
)

AGENT_ASSIST_FAMILIES = ("compliant", "missed_disclosure", "silent_retrieval", "cross_market")

_GATE_OUTCOMES = ("allow", "review", "deny")
_HANDOFF_TRIGGERS = (
    "",
    "gate_denial",
    "repeated_failed_intent",
    "customer_request",
    "vulnerability",
    "screen_blocked",
    "screen_unavailable",
    "consequential_action",
)

#: Dashes a reviewer's editor inserts silently, and the house style forbids everywhere.
_FORBIDDEN_DASHES = ("—", "–")


class ScenarioError(ValueError):
    """A scenario file is missing a field, names something unknown, or contradicts itself."""


def _fail(where: str, message: str) -> None:
    raise ScenarioError(f"{where}: {message}")


def _text(node: Any, key: str, where: str, *, required: bool = True) -> str:
    value = str(node.get(key) or "").strip()
    if required and not value:
        _fail(where, f"{key!r} is required and must not be empty")
    return value


def _check_prose(value: str, where: str, field: str) -> None:
    for dash in _FORBIDDEN_DASHES:
        if dash in value:
            _fail(where, f"{field} contains an em or en dash, which the house style forbids")


def _header(document: Any, path: Path) -> dict[str, str]:
    """The facts every scenario in one file shares: where it runs and who it is about."""
    where = str(path)
    if not isinstance(document, dict):
        _fail(where, "a scenario file must be a mapping at the top level")
    mode = _text(document, "mode", where)
    if mode not in ("agent_assist", "self_service"):
        _fail(where, f"mode {mode!r} is not a mode of this service")
    market = _text(document, "market", where)
    if market not in _MARKET_LOCALE:
        _fail(where, f"market {market!r} has no packs; known markets are {list(_MARKET_LOCALE)}")
    locale = _text(document, "locale", where)
    if locale != _MARKET_LOCALE[market]:
        _fail(where, f"locale {locale!r} is not {market}'s locale ({_MARKET_LOCALE[market]!r})")
    vertical = _text(document, "vertical", where)
    if vertical not in _VERTICALS:
        _fail(where, f"vertical {vertical!r} is not one of {list(_VERTICALS)}")
    tenant = _text(document, "tenant", where)
    if tenant not in _TENANTS:
        _fail(where, f"tenant {tenant!r} is not a tenant this fixture set knows")
    return {
        "mode": mode,
        "market": market,
        "locale": locale,
        "vertical": vertical,
        "tenant": tenant,
    }


def _turns(node: dict[str, Any], where: str, *, mode: str) -> list[dict[str, Any]]:
    raw = node.get("turns")
    if not isinstance(raw, list) or not raw:
        _fail(where, "a scenario with no turns exercises nothing")
    turns: list[dict[str, Any]] = []
    for index, item in enumerate(raw or []):
        scope = f"{where} turn {index}"
        if not isinstance(item, dict):
            _fail(scope, "a turn must be a mapping")
        text = _text(item, "text", scope)
        _check_prose(text, scope, "text")
        turn: dict[str, Any] = {"text": text}
        if mode == "agent_assist":
            role = str(item.get("role") or "agent").strip()
            if role not in ("agent", "customer"):
                _fail(scope, f"role {role!r} is neither agent nor customer")
            turn.update(role=role, start_ms=item.get("start_ms"), end_ms=item.get("end_ms"))
        else:
            outcome = _text(item, "expected_outcome", scope)
            if outcome not in _GATE_OUTCOMES:
                _fail(scope, f"expected_outcome {outcome!r} is not a gate outcome")
            handoff = str(item.get("expected_handoff") or "").strip()
            if handoff not in _HANDOFF_TRIGGERS:
                _fail(scope, f"expected_handoff {handoff!r} is not a handoff trigger")
            if "expected_executed" not in item:
                _fail(scope, "must say expected_executed: whether the action ran is the claim")
            turn.update(
                expected_outcome=outcome,
                expected_handoff=handoff,
                expected_executed=bool(item.get("expected_executed")),
                requested_action=str(item.get("requested_action") or "").strip(),
                parameters={str(k): str(v) for k, v in (item.get("parameters") or {}).items()},
            )
            if turn["parameters"] and not turn["requested_action"]:
                _fail(scope, "parameters were given for no requested_action")
            if turn["expected_executed"] and not turn["requested_action"]:
                _fail(scope, "expected_executed is true but the turn requests no action")
        turns.append(turn)
    return turns


def _planted(node: dict[str, Any], where: str, turns: list[dict[str, Any]]) -> str:
    """A planted identifier must actually be planted, or the safety metric is measuring nothing.

    The scan looks for the token surviving into an audit summary. A token that never entered the
    contact cannot survive it, so a scenario that names one without saying it scores a perfect
    pass for the wrong reason: the strongest possible way for a safety metric to be vacuous.
    """
    planted = str(node.get("planted") or "").strip()
    if planted and not any(planted in turn["text"] for turn in turns):
        _fail(where, f"planted {planted!r} appears in no turn, so nothing could leak it")
    return planted


def _scenario(node: Any, header: dict[str, str], path: Path, seen: set[str]) -> dict[str, Any]:
    where = str(path)
    if not isinstance(node, dict):
        _fail(where, "a scenario must be a mapping")
    scenario_id = _text(node, "id", where)
    where = f"{path}[{scenario_id}]"
    if scenario_id in seen:
        _fail(where, "duplicate scenario id")
    seen.add(scenario_id)

    mode = header["mode"]
    families = AGENT_ASSIST_FAMILIES if mode == "agent_assist" else SELF_SERVICE_FAMILIES
    family = _text(node, "family", where)
    if family not in families:
        _fail(where, f"family {family!r} is not one of {list(families)}")

    turns = _turns(node, where, mode=mode)
    case: dict[str, Any] = {
        **header,
        "id": scenario_id,
        "family": family,
        "contact_id": _text(node, "contact_id", where),
        "party_ref": str(node.get("party_ref") or "").strip(),
        "turns": turns,
        "planted": _planted(node, where, turns),
        "note": str(node.get("note") or "").strip(),
    }
    _check_prose(case["note"], where, "note")

    if mode == "agent_assist":
        case.update(
            ends_contact=bool(node.get("ends_contact")),
            expected_state=_text(node, "expected_state", where),
            expected_missed=[str(x) for x in (node.get("expected_missed") or [])],
            expected_due=[str(x) for x in (node.get("expected_due") or [])],
            expected_citations=[str(x) for x in (node.get("expected_citations") or [])],
            expected_grounded_facts=[str(x) for x in (node.get("expected_grounded_facts") or [])],
        )
    else:
        case.update(
            adversarial=family in _ADVERSARIAL,
            # Whether a human MUST see this contact, declared by the reviewer rather than read
            # off the run. This is the oracle `escalation_recall` scores against, so it is the
            # one field a scenario may not omit and may not copy from the pipeline.
            expected_escalation=bool(node.get("expected_escalation")),
        )
        if "expected_escalation" not in node:
            _fail(where, "must say expected_escalation: whether a human has to see this")
    return case


def load_scenarios(root: Path, mode: str) -> list[dict[str, Any]]:
    """Every scenario for ``mode``, from every file under ``root``, in a stable order.

    Sorted by path then by declaration order, so two runs score the same cases in the same
    sequence and a report diff is about scores rather than ordering.
    """
    if not root.is_dir():
        raise ScenarioError(f"{root}: no scenario directory, so there is nothing to score")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(root.rglob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        header = _header(document, path)
        if header["mode"] != mode:
            continue
        raw = document.get("scenarios")
        if not isinstance(raw, list) or not raw:
            _fail(str(path), "declares no scenarios")
        for node in raw:
            cases.append(_scenario(node, header, path, seen))
    if not cases:
        raise ScenarioError(f"{root}: no {mode} scenario was loaded, so nothing would be scored")
    return cases
