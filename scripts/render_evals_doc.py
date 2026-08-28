#!/usr/bin/env python3
"""Regenerate the derived half of ``docs/evals.md`` from the rubrics, scenarios and floors.

A page that lists metrics and thresholds by hand goes stale the first time a bar moves, and
nothing notices. This repository already had that: ``docs/model-card.md`` names the metrics with
no thresholds beside them and nothing checks it against the rubrics. So the derivation is a
command, and ``--check`` makes staleness a gate failure rather than something a reader
discovers, which is the pattern ``org-metadata/scripts/render-org-profile.py`` established for
the public front page.

Only the sections named in :data:`BLOCKS` are generated. Everything else on the page is
hand-written prose addressed to a reviewer, and this script does not touch it: the point is a
document a person wrote, whose FACTS cannot drift from the artifacts they describe.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
DOC = _REPO_ROOT / "docs" / "evals.md"
RUBRICS = _REPO_ROOT / "eval" / "rubrics"
SCENARIOS = _REPO_ROOT / "eval" / "scenarios"
FLOORS = _REPO_ROOT / "config" / "quality-floors.toml"

#: The headings this script owns. Each runs from its heading to the next `\n## `.
BLOCKS = (
    "## What is measured, and against what bar",
    "## What is exercised",
    "## Where the quality bars come from",
)

_RUBRIC_TITLES = {
    "agent_assist": "Agent assist (bundle `contact-centre-conversations-agent-assist`)",
    "self_service": "Self service (bundle `contact-centre-conversations-self-service`)",
}


def _rubric_rows(rubric: str) -> list[tuple[str, float, str]]:
    """Every metric a rubric gates, with its bar and the one-line reason beside it."""
    rows: list[tuple[str, float, str]] = []
    for path in sorted((RUBRICS / rubric).glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows.append(
            (
                str(document["metric"]),
                float(document["threshold"]),
                " ".join(str(document.get("description", "")).split()),
            )
        )
        for name, node in (document.get("companion_metrics") or {}).items():
            rows.append(
                (
                    str(name),
                    float((node or {}).get("threshold", 0.0)),
                    " ".join(str((node or {}).get("description", "")).split()),
                )
            )
    return sorted(rows)


def _metrics_block() -> list[str]:
    lines = [BLOCKS[0], ""]
    lines += [
        "Every bar below lives in `eval/rubrics/<mode>/*.yaml` next to the argument for it, and",
        "the runner reads it from there. A metric with no reviewed bar, and a bar nothing",
        "measures, both fail the build: see `tests/unit/test_eval_rubrics.py`.",
        "",
        "The two modes are two separately gated releases, so they share no metric name. A shared",
        "row would let a strong agent-assist result carry a weak customer-facing one.",
        "",
    ]
    for rubric, title in _RUBRIC_TITLES.items():
        lines += [f"### {title}", "", "| Metric | Bar | What it gates |", "|---|---|---|"]
        for metric, threshold, description in _rubric_rows(rubric):
            lines.append(f"| `{metric}` | {threshold:g} | {description} |")
        lines.append("")
    return lines


def _scenario_documents() -> list[dict[str, Any]]:
    return [
        yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in sorted(SCENARIOS.rglob("*.yaml"))
    ]


def _scenarios_block() -> list[str]:
    documents = _scenario_documents()
    combos: Counter[tuple[str, str, str]] = Counter()
    families: Counter[tuple[str, str]] = Counter()
    for document in documents:
        mode = str(document["mode"])
        for case in document.get("scenarios") or []:
            combos[(mode, str(document["vertical"]), str(document["market"]))] += 1
            families[(mode, str(case["family"]))] += 1

    lines = [BLOCKS[1], ""]
    lines += [
        "Scenarios are YAML under `eval/scenarios/`, grouped by vertical and market, with prose",
        "at the top of every file explaining what that family covers and why a case expects what",
        "it expects. Every expected label is written by hand from the packs: a metric scored",
        "against the pipeline's own verdict is a tautology with a threshold.",
        "",
        "| Mode | Vertical | Market | Scenarios |",
        "|---|---|---|---|",
    ]
    for (mode, vertical, market), count in sorted(combos.items()):
        lines.append(f"| {mode} | {vertical} | {market} | {count} |")
    lines += ["", "| Mode | Family | Scenarios |", "|---|---|---|"]
    for (mode, family), count in sorted(families.items()):
        lines.append(f"| {mode} | `{family}` | {count} |")
    lines.append("")
    return lines


def _floors_block() -> list[str]:
    document = tomllib.loads(FLOORS.read_text(encoding="utf-8"))
    lines = [BLOCKS[2], ""]
    lines += [
        "The deterministic metrics above answer whether a turn was allowed, grounded, cited and",
        "clean. A reply can be all four and still be useless, so the rest is judged, against",
        f"floors owned by {document.get('owner', 'model risk')} in `config/quality-floors.toml`.",
        "",
        "A score at or above the target is full quality. Below the floor the profile must not",
        "serve that vertical at all. Between them it is DEGRADED: usable, and visibly worse.",
        "",
        "| Vertical | Floor | Target | Why |",
        "|---|---|---|---|",
    ]
    for name, entry in sorted((document.get("verticals") or {}).items()):
        note = " ".join(str(entry.get("note", "")).split())
        lines.append(f"| `{name}` | {entry['floor']:g} | {entry['target']:g} | {note} |")
    lines.append("")
    return lines


def render(published: str) -> str:
    """Replace each owned block in ``published`` with its derived text."""
    rendered = published
    for heading, block in zip(
        BLOCKS, (_metrics_block(), _scenarios_block(), _floors_block()), strict=True
    ):
        rendered = _replace(rendered, heading, block)
    return rendered


def _bounds(text: str, heading: str) -> tuple[int, int]:
    start = text.find(heading)
    if start < 0:
        raise SystemExit(f"FAIL docs/evals.md has no section {heading!r}")
    following = text.find("\n## ", start + len(heading))
    return start, len(text) if following < 0 else following + 1


def _replace(text: str, heading: str, block: list[str]) -> str:
    start, end = _bounds(text, heading)
    return text[:start] + "\n".join(block).rstrip() + "\n\n" + text[end:]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail instead of writing when the published page is stale.",
    )
    args = parser.parse_args(argv)

    published = DOC.read_text(encoding="utf-8")
    rendered = render(published)
    if not args.check:
        DOC.write_text(rendered, encoding="utf-8")
        print(f"wrote {DOC}")
        return 0
    if rendered != published:
        print("FAIL docs/evals.md derived sections are stale:")
        diff = difflib.unified_diff(
            published.splitlines(), rendered.splitlines(), "published", "derived", lineterm="", n=1
        )
        for line in list(diff)[:40]:
            print("   " + line)
        print("   run scripts/render_evals_doc.py")
        return 1
    print("docs/evals.md matches the rubrics, scenarios and floors")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
