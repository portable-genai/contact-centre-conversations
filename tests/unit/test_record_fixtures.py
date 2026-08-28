"""The recording step's own guards, tested offline because the step itself cannot be.

``scripts/record_gemini_fixtures.py`` is an authoring step: it needs the managed profile and
real credentials, so nothing in the gate can run it end to end. That is exactly how its capture
path rotted once already (a wrapper that called the patched attribute instead of the captured
original, and a ``__dict__`` read on a slots dataclass), which is the strongest argument for
testing every part of it that CAN run offline: the profile refusal that keeps it out of the
gate, and the scrub that decides whether a recording may be committed at all.

The scrub is the load-bearing half. A recording that leaked personal data would commit model
output carrying it into the repository, so ``_scrub`` must refuse the WHOLE batch on any hit;
a filter that dropped the offending row would write a scrubbed lie.
"""

from __future__ import annotations

import pytest
import record_gemini_fixtures as recorder

from contact_centre_conversations.domain.suggestions import MAX_SUGGESTION_CHARS


def _row(text: str | None, case_id: str = "case-1") -> dict:
    return {"case_id": case_id, "response": None if text is None else {"text": text}}


# ------------------------------------------------------------------ the profile refusal
def test_recording_refuses_off_the_managed_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """The step makes real model calls, so a laptop profile must not be able to start it."""
    monkeypatch.setenv("CONTACT_PROFILE", "local")
    assert recorder.main([]) == 2


# ------------------------------------------------------------------ the scrub gate
def test_a_clean_batch_passes_the_scrub() -> None:
    recorder._scrub([_row("The posted balance is what the app shows.")], planted=set())


def test_a_reply_carrying_personal_data_refuses_the_whole_batch() -> None:
    """The runtime pattern set is the scanner, so the gate's detector cannot drift from it."""
    rows = [_row("all fine"), _row("Your NRIC S1234567D is on file.", case_id="case-2")]
    with pytest.raises(recorder.RecordingRefused, match="case-2"):
        recorder._scrub(rows, planted=set())


def test_a_reply_echoing_a_planted_identifier_refuses_the_whole_batch() -> None:
    """The planted scan is the oracle the pattern pack cannot satisfy by agreeing with itself."""
    with pytest.raises(recorder.RecordingRefused, match="planted"):
        recorder._scrub([_row("the reference was TOKEN-XYZ")], planted={"TOKEN-XYZ"})


def test_an_overlong_reply_refuses_the_whole_batch() -> None:
    """A reply the validator would discard is not evidence worth committing."""
    with pytest.raises(recorder.RecordingRefused, match="characters"):
        recorder._scrub([_row("x" * (MAX_SUGGESTION_CHARS + 1))], planted=set())


def test_a_recorded_refusal_needs_no_scrub_and_passes() -> None:
    """A model that declined produced no text to leak; the scrub must not trip over it."""
    recorder._scrub([_row(None)], planted={"TOKEN-XYZ"})
