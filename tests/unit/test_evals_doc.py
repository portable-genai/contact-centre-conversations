"""``docs/evals.md`` cannot drift from the artifacts that actually gate the build.

A page listing metrics and thresholds by hand goes stale the first time a bar moves, and nothing
notices. This repository already has one: `docs/model-card.md` names the metrics with no
thresholds beside them and nothing checks it. So the derived half of the evals page is generated,
and the check runs HERE rather than only as a make target, because a check somebody has to
remember to run is a check that stops being run.

That is this repo's own rule, stated in `scripts/check_docs_links.py`: the same function the
command calls is called from the offline gate, so a stale page fails the build.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import render_evals_doc as doc  # noqa: E402


def test_the_published_page_matches_the_rubrics_scenarios_and_floors() -> None:
    assert doc.main(["--check"]) == 0, (
        "docs/evals.md is stale: run scripts/render_evals_doc.py and commit the result"
    )


def test_the_page_is_registered_as_a_required_artifact() -> None:
    assert doc.DOC.is_file()


@pytest.mark.parametrize("heading", doc.BLOCKS)
def test_every_generated_section_is_present_on_the_page(heading: str) -> None:
    """A section quietly deleted would stop being regenerated and stop being wrong out loud."""
    assert heading in doc.DOC.read_text(encoding="utf-8")


def test_the_check_actually_fails_on_a_stale_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Watch it go red: a check that only ever passes has not checked anything.

    The published page is copied, one generated fact is edited out, and the check must refuse.
    """
    stale = tmp_path / "evals.md"
    published = doc.DOC.read_text(encoding="utf-8")
    stale.write_text(published.replace("| 0.99 |", "| 0.10 |"), encoding="utf-8")
    monkeypatch.setattr(doc, "DOC", stale)
    assert doc.main(["--check"]) == 1


def test_every_metric_the_rubrics_gate_appears_on_the_page() -> None:
    """The page is where a reviewer looks for the full list, so a missing row is a real gap."""
    page = doc.DOC.read_text(encoding="utf-8")
    for rubric in ("agent_assist", "self_service"):
        for metric, _threshold, _description in doc._rubric_rows(rubric):
            assert f"`{metric}`" in page, f"{metric} is gated but not documented"


def test_the_page_names_what_is_not_measured() -> None:
    """An eval page that lists only what it covers reads as a claim of completeness."""
    page = doc.DOC.read_text(encoding="utf-8")
    assert "## What is not measured" in page
    for gap in ("voice", "HK and AU"):
        assert gap in page
