#!/usr/bin/env python3
"""Render the eval artifact to ONE self-contained page a reviewer can actually read.

Server-side rendering with the stdlib alone: no framework, no bundler, no network, no template
engine. That is the same rule the demo renderer follows and for the same reason, plus one more
that matters here: an eval report is something a conduct reviewer opens, forwards, and keeps, so
it has to be a single file that works from a mail attachment on a locked-down laptop.

The layout is the catalog's audit-first order, applied to a run rather than to a decision:

1. the RESULT, per rubric, with each metric against its bar;
2. the ROLLUPS a reader groups by (vertical, market, family), because "which metric failed" is
   rarely the useful question and "what KIND of conversation failed" usually is;
3. the CONVERSATIONS themselves, collapsed, expanding to what was said, what a reviewer
   expected, what actually happened, which citations were shown, and what to change.

Collapsed by default and deliberately: thirty conversations expanded is a wall nobody reads, and
the failures are the ones that should be open. Failing cases start open; passing ones do not.

This script only PAINTS. `eval/run_eval.py --emit` produces the JSON, so the report cannot show
one thing while the eval computed another.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


# Kept in one plain string (never an f-string) so a CSS brace can never be confused with a
# template brace by the generator that renders this repo.
STYLE = """
:root {
  --ink: #16181d; --ink-2: #4a5160; --ink-3: #8a93a6;
  --line: #e3e7ee; --bg: #ffffff; --bg-2: #f7f9fc;
  --ok: #1a7f4b; --ok-bg: #e8f6ee; --bad: #b3261e; --bad-bg: #fdecea;
  --warn: #8a6100; --shadow: 0 1px 2px rgba(16,24,40,.06), 0 1px 3px rgba(16,24,40,.1);
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 2rem 1.25rem 4rem; background: var(--bg-2); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
}
main { max-width: 1080px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
h2 { font-size: 1.05rem; margin: 2rem 0 .75rem; letter-spacing: -.005em; }
h3 { font-size: .95rem; margin: 1.25rem 0 .5rem; }
.sub { color: var(--ink-2); margin: 0 0 1.5rem; }
.panel {
  background: var(--bg); border: 1px solid var(--line); border-radius: 10px;
  box-shadow: var(--shadow); padding: 1rem 1.15rem; margin-bottom: 1rem;
}
.tiles {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .75rem;
}
.tile {
  background: var(--bg); border: 1px solid var(--line);
  border-radius: 10px; padding: .8rem .9rem;
}
.tile .n { font-size: 1.5rem; font-weight: 600; letter-spacing: -.02em; }
.tile .k {
  color: var(--ink-3); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em;
}
.pill {
  display: inline-block; padding: .1rem .5rem; border-radius: 999px;
  font-size: .74rem; font-weight: 600; letter-spacing: .03em;
}
.pass { background: var(--ok-bg); color: var(--ok); }
.fail { background: var(--bad-bg); color: var(--bad); }
table { width: 100%; border-collapse: collapse; font-size: .9rem; }
th, td { text-align: left; padding: .45rem .5rem; border-bottom: 1px solid var(--line); }
th {
  color: var(--ink-3); font-weight: 600; font-size: .76rem;
  text-transform: uppercase; letter-spacing: .04em;
}
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar {
  position: relative; height: 7px; background: var(--line);
  border-radius: 4px; overflow: visible; min-width: 110px;
}
.bar > .fill {
  position: absolute; inset: 0 auto 0 0; border-radius: 4px; background: var(--ok);
}
.bar > .fill.bad { background: var(--bad); }
.bar > .thr {
  position: absolute; top: -3px; bottom: -3px; width: 2px;
  background: var(--ink); opacity: .55;
}
details {
  border: 1px solid var(--line); border-radius: 9px;
  background: var(--bg); margin-bottom: .5rem;
}
details[open] { box-shadow: var(--shadow); }
summary {
  cursor: pointer; padding: .6rem .85rem; display: flex; gap: .6rem; align-items: center;
  font-size: .92rem; list-style: none;
}
summary::-webkit-details-marker { display: none; }
summary::before { content: "\\25B8"; color: var(--ink-3); font-size: .8rem; }
details[open] > summary::before { content: "\\25BE"; }
summary .id { font-weight: 600; }
summary .meta { color: var(--ink-3); font-size: .8rem; margin-left: auto; }
.body { padding: 0 .85rem .85rem; border-top: 1px solid var(--line); }
.turn { border-left: 3px solid var(--line); padding: .5rem 0 .5rem .7rem; margin: .7rem 0; }
.turn.miss { border-left-color: var(--bad); }
.said { margin: 0 0 .35rem; }
.kv { color: var(--ink-2); font-size: .84rem; margin: .1rem 0; }
.kv b { color: var(--ink); font-weight: 600; }
.note { color: var(--ink-2); font-size: .87rem; font-style: italic; margin: .5rem 0 0; }
.rem { color: var(--warn); font-size: .84rem; margin: .3rem 0 0; }
code {
  background: var(--bg-2); border: 1px solid var(--line);
  border-radius: 4px; padding: .05rem .3rem; font-size: .85em;
}
.muted { color: var(--ink-3); }
"""


def _bar(score: float, threshold: float, passed: bool) -> str:
    """A metric's score against its bar, with the threshold marked so a near miss is visible."""
    width = max(0.0, min(1.0, score)) * 100
    mark = max(0.0, min(1.0, threshold)) * 100
    klass = "fill" if passed else "fill bad"
    return (
        f"<div class='bar'><div class='{klass}' style='width:{width:.1f}%'></div>"
        f"<div class='thr' style='left:{mark:.1f}%'></div></div>"
    )


def _metric_table(metrics: list[dict[str, Any]]) -> str:
    rows = []
    for metric in metrics:
        verdict = "pass" if metric["passed"] else "fail"
        rem = (
            f"<div class='rem'>change: {esc(metric['remediation'])}</div>"
            if metric.get("remediation")
            else ""
        )
        rows.append(
            "<tr>"
            f"<td><code>{esc(metric['metric'])}</code>{rem}</td>"
            f"<td class='num'>{metric['score']:.3f}</td>"
            f"<td class='num muted'>{metric['threshold']:.2f}</td>"
            f"<td>{_bar(metric['score'], metric['threshold'], metric['passed'])}</td>"
            f"<td><span class='pill {verdict}'>{verdict.upper()}</span></td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Metric</th><th class='num'>Score</th>"
        "<th class='num'>Bar</th><th>Against the bar</th><th>Result</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _rollup(rows: list[dict[str, Any]], key: str, label: str) -> str:
    """Group the conversations by one dimension. What KIND of conversation failed."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(str(row[key]), []).append(row)
    body = []
    for name, members in sorted(groups.items()):
        failed = [m for m in members if not m["passed"]]
        verdict = "fail" if failed else "pass"
        body.append(
            "<tr>"
            f"<td><code>{esc(name)}</code></td>"
            f"<td class='num'>{len(members)}</td>"
            f"<td class='num'>{len(failed)}</td>"
            f"<td><span class='pill {verdict}'>{'FAIL' if failed else 'PASS'}</span></td>"
            "</tr>"
        )
    return (
        f"<h3>{esc(label)}</h3><table><thead><tr><th>{esc(label)}</th>"
        "<th class='num'>Cases</th><th class='num'>Failing</th><th>Result</th>"
        f"</tr></thead><tbody>{''.join(body)}</tbody></table>"
    )


def _turn(turn: dict[str, Any]) -> str:
    expected, actual = turn.get("expected") or {}, turn.get("actual") or {}
    missed = [k for k, v in expected.items() if actual.get(k) != v]
    lines = []
    for key in sorted(set(expected) | set(actual)):
        if key not in expected:
            continue
        same = actual.get(key) == expected[key]
        marker = "" if same else " &larr; differs"
        lines.append(
            f"<div class='kv'><b>{esc(key)}</b>: expected <code>{esc(expected[key])}</code>, "
            f"got <code>{esc(actual.get(key))}</code>{marker}</div>"
        )
    cites = "".join(
        f"<div class='kv'>cited <code>{esc(c['source_id'])}</code> "
        f"{esc(c.get('title', ''))} <span class='muted'>{esc(c.get('source_ref', ''))}</span></div>"
        for c in turn.get("citations") or ()
    )
    notes = "".join(f"<div class='kv muted'>{esc(n)}</div>" for n in turn.get("notes") or ())
    klass = "turn miss" if missed else "turn"
    return (
        f"<div class='{klass}'><p class='said'>&ldquo;{esc(turn['text'])}&rdquo;</p>"
        f"{''.join(lines)}{cites}{notes}</div>"
    )


def _case(case: dict[str, Any]) -> str:
    failing = [d for d in case["dimensions"] if not d["passed"]]
    verdict = "fail" if failing else "pass"
    open_attr = " open" if failing else ""
    note = f"<p class='note'>{esc(case['note'])}</p>" if case.get("note") else ""
    dims = "".join(
        f"<div class='kv'><span class='pill {'fail' if not d['passed'] else 'pass'}'>"
        f"{'FAIL' if not d['passed'] else 'PASS'}</span> <code>{esc(d['metric'])}</code>"
        + (f" {esc(d['detail'])}" if d.get("detail") else "")
        + (
            f"<div class='rem'>change: {esc(d['remediation'])}</div>"
            if d.get("remediation")
            else ""
        )
        + "</div>"
        for d in case["dimensions"]
    )
    turns = "".join(_turn(t) for t in case["turns"])
    return (
        f"<details{open_attr}><summary>"
        f"<span class='pill {verdict}'>{verdict.upper()}</span>"
        f"<span class='id'>{esc(case['case_id'])}</span>"
        f"<span class='meta'>{esc(case['family'])} &middot; {esc(case['vertical'])} &middot; "
        f"{esc(case['market'])} &middot; {esc(case['tenant'])}</span>"
        f"</summary><div class='body'>{note}{dims}{turns}</div></details>"
    )


def render(payload: dict[str, Any]) -> str:
    runs = payload.get("runs") or []
    if not runs:
        # A page summing nothing would show zero failures and an overall PASS, which is a
        # verdict over no evidence. The writer refuses to produce such a file; refuse to paint
        # one anyway, in case the input did not come from the writer.
        raise SystemExit("the report contains no runs, so there is nothing to render")
    total = sum(len(run["cases"]) for run in runs)
    failing = sum(1 for run in runs for row in run["rows"] if not row["passed"])
    metrics_failing = sum(1 for run in runs for m in run["metrics"] if not m["passed"])
    verdict = "fail" if metrics_failing else "pass"

    sections = []
    for run in runs:
        cases = run["cases"]
        rollups = "".join(
            _rollup(run["rows"], key, label)
            for key, label in (
                ("vertical", "Vertical"),
                ("market", "Market"),
                ("family", "Scenario family"),
            )
        )
        sections.append(
            f"<h2>{esc(run['rubric'])}</h2>"
            f"<div class='panel'>{_metric_table(run['metrics'])}"
            f"<p class='kv muted'>run <code>{esc(run['run_id'])}</code> &middot; "
            f"corpus <code>{esc(run['dataset_digest'][:12])}</code> &middot; "
            f"scored by <code>{esc(run['evaluator'])}</code></p></div>"
            f"<div class='panel'>{rollups}</div>"
            f"<h3>Conversations ({len(cases)})</h3>" + "".join(_case(case) for case in cases)
        )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>Contact Centre AI: evaluation report</title>"
        f"<style>{STYLE}</style></head><body><main>"
        "<h1>Contact Centre AI: evaluation report</h1>"
        "<p class='sub'>Two modes, scored separately, because they are two gated releases. "
        "Failing conversations are expanded; the rest are one click away.</p>"
        "<div class='tiles'>"
        f"<div class='tile'><div class='k'>Overall</div>"
        f"<div class='n'><span class='pill {verdict}'>{verdict.upper()}</span></div></div>"
        f"<div class='tile'><div class='k'>Conversations</div><div class='n'>{total}</div></div>"
        f"<div class='tile'><div class='k'>Failing</div><div class='n'>{failing}</div></div>"
        f"<div class='tile'><div class='k'>Metrics below bar</div>"
        f"<div class='n'>{metrics_failing}</div></div>"
        "</div>" + "".join(sections) + "</main></body></html>"
    )


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: render_eval_report.py <report.json> <out-dir>", file=sys.stderr)
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    out_dir = Path(argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(render(payload), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv))
