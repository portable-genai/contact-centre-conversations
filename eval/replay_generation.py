"""Score the rubrics against a REAL model's words, offline, by replaying recorded output.

Every deterministic metric scores ``LocalTemplateGenerationAdapter``, a template quoter that
structurally cannot invent a figure. That makes the citation and grounding metrics a measurement
of the VALIDATOR rather than of a model's restraint, which the model card says plainly and which
is the largest honest gap in the suite.

The fix cannot be "call Gemini in the gate": the gate must pass with no network, no credentials
and no cloud SDK. So a real call is made ONCE, by hand, by ``scripts/record_gemini_fixtures.py``,
and its output is committed. This adapter replays that recording, so the same rubrics and the
same hand-written labels score real model text with nothing reachable.

Eval-only, and deliberately not a product adapter. It lives here rather than under ``src/``
because nothing a deployment binds should be able to serve pre-recorded answers to a customer,
and it lives here rather than under ``tests/`` because the eval must not import the test tree.

Fails closed in the one way that matters: a prompt with no recording RAISES, naming the case. It
never falls back to the template drafter and it never reaches the network. A replay that quietly
substituted a different drafter would report a score for a model that produced none of it.

One subtlety keeps that honest end to end. The kernel treats ANY generation failure as silence,
deliberately: for the product, a model outage must degrade to "no suggestion", never to an
unvalidated fallback. That product property would swallow this adapter's raise, and silence is
scoreable, so a stale recording would grade as a model that declined everything and pass
wherever silence was the expected answer. So every miss is also recorded in :attr:`MISSES`, and
``run_eval.py`` fails the whole replay run when the list is non-empty, whatever the metrics say.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, ClassVar

from contact_centre_conversations.config import Settings
from contact_centre_conversations.domain.models import RetrievedPassage

__all__ = ["FIXTURE", "ReplayGenerationAdapter", "recording_key"]

FIXTURE = Path(__file__).resolve().parent / "datasets" / "gemini_replay.jsonl"


def recording_key(model: str, prompt: str, passages: Sequence[RetrievedPassage]) -> str:
    """The identity of one generation call: the model, the prompt, and what it was given.

    All three, because all three change the answer. Keying on the prompt alone would replay one
    market's recorded reply for another market's passages and call it evidence.
    """
    ids = ",".join(sorted(passage.citation.source_id for passage in passages))
    return hashlib.sha256(f"{model}\n{prompt}\n{ids}".encode()).hexdigest()


class ReplayGenerationAdapter:
    """Satisfies GenerationPort by replaying a recorded managed-model response."""

    #: Every key that had no recording, across all instances of a run. Class-level because the
    #: container constructs the instance and the runner never holds it; the runner clears this
    #: before a replay run and fails the run if anything lands here, because the kernel converts
    #: the raise below into silence and silence is scoreable. See the module docstring.
    MISSES: ClassVar[list[str]] = []

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rows: dict[str, Any] | None = None

    def _recordings(self) -> dict[str, Any]:
        if self._rows is not None:
            return self._rows
        if not FIXTURE.exists():
            raise RuntimeError(
                f"no recorded model output at {FIXTURE}. Record it once with "
                "`CONTACT_PROFILE=gcp python scripts/record_gemini_fixtures.py`, review the "
                "scrubbed result, and commit it. Until then the rubrics score the offline "
                "template drafter, which is what the model card says they do."
            )
        rows: dict[str, Any] = {}
        for raw in FIXTURE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            rows[str(row["key"])] = row
        if not rows:
            raise RuntimeError(f"{FIXTURE} contains no recordings, so nothing can be replayed")
        self._rows = rows
        return rows

    def draft(self, prompt: str, passages: Sequence[RetrievedPassage]) -> Mapping[str, Any] | None:
        key = recording_key(self._settings.model, prompt, passages)
        try:
            row = self._recordings().get(key)
        except RuntimeError:
            # An unreadable or empty fixture is a miss for every draft, not only for this key.
            ReplayGenerationAdapter.MISSES.append(key)
            raise
        if row is None:
            ReplayGenerationAdapter.MISSES.append(key)
            raise RuntimeError(
                f"no recorded model output for {key[:12]} (model {self._settings.model!r}). "
                "The recording is out of date with the corpus or the prompt. Re-record rather "
                "than falling back: a replay that substituted another drafter would report a "
                "score for a model that produced none of it."
            )
        # A recorded `null` is a real answer: the model declined, and the pipeline treats that
        # as no suggestion. Replaying it as an error would hide a case worth scoring.
        response = row.get("response")
        return response if isinstance(response, Mapping) else None
