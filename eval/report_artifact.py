"""Per-conversation eval detail: the record a human reviews, beside the score a gate reads.

``agent_eval_kit.EvalReport`` is per-METRIC and deliberately so: it is what a promotion gate
consumes, it is frozen, it is pure stdlib by hard constraint, and about twenty-five repositories
share it. Adding per-case rows to it would change the wire shape the gate client parses. So the
drill-down lives here, in this repository, and the kit's report points at it through
``artifact_refs``, which is exactly what that field is for.

The vocabulary is borrowed rather than invented. E3 (``conversation-qa-scorecard``) already
models a per-conversation verdict with ``Scorecard`` for the detail, ``ScorecardRow`` for the
flat index projection, and ``EvidenceSpan`` for pointing at the words. Two repositories in one
estate should not have two names for one idea, so these mirror them, including the privacy rule
that comes with the split: the INDEX carries ids and scores, and only the DETAIL carries what
was said.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "SCHEMA_VERSION",
    "CaseResult",
    "CaseRow",
    "DimensionScore",
    "EvalRunArtifact",
    "TurnRecord",
    "write_artifact",
]

#: Mirrors the kit's own ``eval-run/v1`` naming, so a reader can tell the two artifacts apart.
SCHEMA_VERSION = "eval-cases/v1"


@dataclass(frozen=True, slots=True)
class TurnRecord:
    """One turn as it actually went, next to what a reviewer said should happen.

    Both halves, always. A drill-down that showed only the outcome would make a reviewer open
    the scenario file in another window to find out whether it was the right one.
    """

    index: int
    text: str
    expected: dict[str, Any] = field(default_factory=dict)
    actual: dict[str, Any] = field(default_factory=dict)
    citations: tuple[dict[str, str], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return all(self.actual.get(key) == value for key, value in self.expected.items())


@dataclass(frozen=True, slots=True)
class DimensionScore:
    """One metric's verdict for ONE case, with what to do about it when it is red.

    ``remediation`` is what makes a red row actionable rather than merely present. A reviewer
    reading a failure needs to know which artifact to change, and that is usually a pack or a
    scenario rather than any code.
    """

    metric: str
    score: float
    threshold: float
    passed: bool
    detail: str = ""
    remediation: str = ""


@dataclass(frozen=True, slots=True)
class CaseResult:
    """The drill-down record for one conversation."""

    case_id: str
    rubric: str
    family: str
    market: str
    vertical: str
    tenant: str
    party_ref: str
    note: str = ""
    turns: tuple[TurnRecord, ...] = ()
    dimensions: tuple[DimensionScore, ...] = ()

    @property
    def passed(self) -> bool:
        return all(dimension.passed for dimension in self.dimensions)

    @property
    def failing(self) -> tuple[DimensionScore, ...]:
        return tuple(d for d in self.dimensions if not d.passed)

    def row(self) -> CaseRow:
        return CaseRow(
            case_id=self.case_id,
            rubric=self.rubric,
            family=self.family,
            market=self.market,
            vertical=self.vertical,
            tenant=self.tenant,
            turn_count=len(self.turns),
            passed=self.passed,
            failing_metrics=tuple(d.metric for d in self.failing),
        )


@dataclass(frozen=True, slots=True)
class CaseRow:
    """The flat index projection: one row per conversation, and NO transcript text.

    The omission is the point, and it is E3's rule rather than a new one. An index is the thing
    that gets exported, pasted into a ticket and kept; shipping utterances into it is how a
    quality programme becomes a data-protection incident. The words live in the detail, one
    click away, where somebody has chosen to look at one conversation.
    """

    case_id: str
    rubric: str
    family: str
    market: str
    vertical: str
    tenant: str
    turn_count: int
    passed: bool
    failing_metrics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvalRunArtifact:
    """One run: the rollups a reader groups by, and the cases they drill into."""

    schema_version: str
    run_id: str
    rubric: str
    dataset: str
    dataset_digest: str
    evaluator: str
    as_of: str
    metrics: tuple[DimensionScore, ...]
    rows: tuple[CaseRow, ...]
    cases: tuple[CaseResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.metrics) and all(metric.passed for metric in self.metrics)


def write_artifact(artifacts: list[EvalRunArtifact], path: Path) -> Path:
    """Write every rubric's artifact to one JSON file, creating the directory if needed.

    One file rather than one per rubric: the two modes are scored separately and reviewed
    together, and a reviewer opening a report wants both in front of them.

    An EMPTY list is refused rather than written. A report file with no runs in it still looks
    like a report: it has a schema version, it parses, and a renderer that summed nothing over
    it would paint zero failures as a pass. The absence of runs is a caller bug, not a result.
    """
    if not artifacts:
        raise ValueError(
            "no run artifacts were produced, so there is no report to write. A report file "
            "with zero runs would read as a clean run rather than as an absent one."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "runs": [asdict(artifact) for artifact in artifacts],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
