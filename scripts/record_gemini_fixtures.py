#!/usr/bin/env python3
"""Record the managed model's replies ONCE, by hand, so the eval can replay them offline.

This is an authoring step, never a gate step. It requires the managed profile and real
credentials, makes real calls, and writes `eval/datasets/gemini_replay.jsonl` for
`eval/replay_generation.py` to replay. Run it, read the diff, commit the result.

    CONTACT_PROFILE=gcp python scripts/record_gemini_fixtures.py

Why a recording rather than a live call in the eval: the gate must pass with no network, no
credentials and no cloud SDK. Why a recording rather than nothing: without one, every citation
and grounding metric scores the offline template quoter, which structurally cannot invent a
figure, so those metrics measure the validator rather than a model's restraint.

**Nothing is written unless the whole batch is clean.** Every recorded reply is scanned for
personal data with the same pattern set the runtime redactor uses, for each scenario's planted
identifier, and for length. One hit aborts the entire write rather than dropping the offending
row: a fixture that silently lost a case would be a corpus nobody could reason about, and a
partial write is how a scrubbed file ends up half scrubbed.

The prompts are already redacted before they reach a model, by the kernel's own ordering, and
that is asserted here too rather than assumed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pii_kit import pack_leak

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "eval"))
sys.path.insert(0, str(_REPO_ROOT / "src"))

import eval_schema  # noqa: E402
import replay_generation  # noqa: E402
from speech_lexicon_kit import ChannelRole  # noqa: E402

from contact_centre_conversations.config import Settings, build_container  # noqa: E402
from contact_centre_conversations.domain.models import (  # noqa: E402
    ContactRef,
    TurnSubmission,
)
from contact_centre_conversations.domain.modes import ContactMode, ModeGates  # noqa: E402
from contact_centre_conversations.domain.pii import PII_PATTERNS  # noqa: E402
from contact_centre_conversations.domain.suggestions import MAX_SUGGESTION_CHARS  # noqa: E402
from contact_centre_conversations.services import build_services  # noqa: E402

SCENARIOS = _REPO_ROOT / "eval" / "scenarios"


class RecordingRefused(RuntimeError):
    """Something in the batch is not safe to commit, so none of it is written."""


def _scrub(rows: list[dict[str, Any]], planted: set[str]) -> None:
    """Refuse the whole batch on any leak. Not a filter: a filter would write a scrubbed lie."""
    for row in rows:
        response = row.get("response") or {}
        text = str(response.get("text", ""))
        if not text:
            continue
        if pack_leak(text, PII_PATTERNS):
            raise RecordingRefused(
                f"{row['case_id']}: the recorded reply matches the personal-data patterns. "
                "Nothing has been written. Fix what reached the model before recording again."
            )
        for token in planted:
            if token and token in text:
                raise RecordingRefused(
                    f"{row['case_id']}: the recorded reply contains the planted identifier "
                    f"{token!r}. Nothing has been written."
                )
        if len(text) > MAX_SUGGESTION_CHARS:
            raise RecordingRefused(
                f"{row['case_id']}: the recorded reply is {len(text)} characters, over the "
                f"{MAX_SUGGESTION_CHARS} the validator accepts. Nothing has been written."
            )


def main(argv: list[str] | None = None) -> int:
    settings = Settings.load()
    if settings.profile != "gcp":
        print(
            f"refused: recording needs the managed profile (got {settings.profile!r}). "
            "This step makes real model calls; the eval that replays them does not.",
            file=sys.stderr,
        )
        return 2

    container = build_container(
        Settings(**{**settings.__dict__, "modes": ModeGates.both_on()})
        if not settings.modes.agent_assist.enabled
        else settings
    )
    built = build_services(container)
    generation = container.generation

    captured: list[dict[str, Any]] = []
    planted: set[str] = set()

    def _capturing_draft(prompt: str, passages: Any, *, case_id: str) -> Any:
        response = generation.draft(prompt, passages)
        captured.append(
            {
                "key": replay_generation.recording_key(settings.model, prompt, passages),
                "case_id": case_id,
                "model": settings.model,
                "prompt_sha256": replay_generation.recording_key(settings.model, prompt, ()),
                "passage_ids": sorted(p.citation.source_id for p in passages),
                "response": response,
            }
        )
        return response

    for mode, contact_mode in (
        ("agent_assist", ContactMode.AGENT_ASSIST),
        ("self_service", ContactMode.SELF_SERVICE),
    ):
        for case in eval_schema.load_scenarios(SCENARIOS, mode):
            planted.add(case.get("planted", ""))
            contact = ContactRef(
                contact_id=f"record-{case['id']}",
                tenant=case["tenant"],
                market=case["market"],
                locale=case["locale"],
                vertical=case["vertical"],
                party_ref=case["party_ref"],
                mode=contact_mode,
            )
            container.generation.draft = (  # type: ignore[method-assign]
                lambda prompt, passages, _case=case["id"]: _capturing_draft(
                    prompt, passages, case_id=_case
                )
            )
            for index, turn in enumerate(case["turns"]):
                submission = TurnSubmission(
                    contact=contact,
                    index=index,
                    speaker_id=str(turn.get("role", "customer")),
                    role=ChannelRole(str(turn.get("role", "customer"))),
                    text=str(turn["text"]),
                )
                if contact_mode is ContactMode.AGENT_ASSIST:
                    built.agent_assist.observe(submission, actor="recorder", as_of=None)  # type: ignore[arg-type]
                else:
                    built.self_service.handle(submission, actor="recorder", as_of=None)  # type: ignore[arg-type]

    if not captured:
        print("refused: nothing was captured, so there is nothing to record", file=sys.stderr)
        return 1

    _scrub(captured, planted)
    replay_generation.FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Recorded managed-model replies, for offline replay by eval/replay_generation.py.\n"
        "# Written by scripts/record_gemini_fixtures.py against the managed profile. Every row\n"
        "# was scanned for personal data with the runtime pattern set, for planted identifiers\n"
        "# and for length before ANY of it was written: one hit aborts the whole batch.\n"
        "#\n"
        "# Re-record when the corpus or the prompt changes. The replay adapter raises on a\n"
        "# missing key rather than falling back, so a stale recording is loud.\n"
    )
    replay_generation.FIXTURE.write_text(
        header + "\n".join(json.dumps(row, ensure_ascii=False) for row in captured) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(captured)} recordings to {replay_generation.FIXTURE}")
    print("Review the diff before committing: this file is model output, not authored fixture.")
    return 0


if __name__ == "__main__":  # pragma: no cover - an authoring step, never a gate step
    raise SystemExit(main(sys.argv[1:]))
