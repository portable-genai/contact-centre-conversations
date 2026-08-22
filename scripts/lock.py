#!/usr/bin/env python3
"""Resolve once into `uv.lock`, export the per-profile lockfiles, and put their headers back.

`uv.lock` is the resolution: ONE file covering every extra, in which a commons dependency is
recorded structurally as `source = { git = "...?rev=<tag>#<40-character commit>" }`. The tag and
the commit sit together, maintained by the resolver. Nothing in this repository has to keep them
equal by hand any more, which is what the previous arrangement spent its whole existence doing.

The `requirements-*.lock` files remain, because they are what the `Dockerfile`, CI and every
`pip install -r` consume, but they are now EXPORTS of `uv.lock` rather than independent
resolutions. Two independent resolutions of the same project could disagree; an export cannot
disagree with what it was exported from.

The header still has to be re-applied here. `uv export`, like the `uv pip compile` it replaced,
REPLACES the output file and writes only its own provenance comment, so every documented run
would otherwise destroy the block below: the explanation of why the commons are pinned by COMMIT
rather than by tag, and the `tag = commit` map that `tests/unit/test_repo_artifacts.py` checks the
pins against. That is not hypothetical. One catalog repo carried exactly that damage, with a red
gate, because somebody ran the raw command and committed the result: `uv` succeeded, the file
looked plausible, and the only symptom was two failing tests in a suite nobody re-read.

So `make lock` runs this script instead of a raw command, and the companion test fails the build
if a lockfile ever appears without its header. Run it after any dependency change and commit
`uv.lock` together with both exports.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

#: Commons packages pinned by git reference. The KNOWN order keeps existing headers stable; any
#: other git-pinned package is appended, because a repo may pin a commons this list has never
#: heard of (one pins `speech-lexicon-kit`) and a fixed allowlist would silently omit it from the
#: map, which is exactly the incompleteness the pin test then fails on.
_KNOWN_ORDER = ("hex-service-kit", "agent-eval-kit", "review-kit", "pii-kit")


def _commons_order(names: set[str]) -> list[str]:
    known = [n for n in _KNOWN_ORDER if n in names]
    return known + sorted(names - set(known))


#: (extra, output file) for each lockfile the repo ships.
_TARGETS = (("dev", "requirements-dev.lock"), ("gcp", "requirements-gcp.lock"))

#: `name[extras] @ git+URL@vTAG`, the shape every commons dependency is declared in.
_GIT_PIN = r"([a-z0-9-]+)(?:\[[^\]]*\])?\s*@\s*git\+\S+?@(v[0-9][^\s\"']*)"

_HEADER = """\
# Exported dependency lock. DO NOT hand-edit: run `make lock` and commit the result.
#
#   uv export --extra {extra} --no-hashes --no-emit-project -o {output}
#
# DERIVED, not resolved here: `uv.lock` is this repository's single resolution and the file to
# read when the two disagree. It covers every extra at once, so the dev and runtime profiles are
# two views of ONE resolution rather than two resolutions that could drift apart. This export
# exists because the Dockerfile, CI and every `pip install -r` consume requirements format.
#
# The export is universal, so ONE file installs across every interpreter in the CI matrix.
# Everything is pinned to an exact version, and the catalog commons are pinned to a 40-character
# COMMIT SHA rather than to the tag `pyproject.toml` names.
#
# A tag is a movable pointer. A lockfile that pins a tag installs whatever that tag points at on
# the day pip runs, so a re-pushed tag changes what ships with NO diff in this file and no way to
# notice: it breaks the reproducible-install claim (practices check D1) exactly where the claim is
# supposed to be strongest. A commit cannot move.
#
# Dereference a tag with `git rev-list -n 1 <tag>`, NOT `git rev-parse <tag>`. These are ANNOTATED
# tags, so `rev-parse` returns the tag OBJECT (a different sha that is not a commit); pinning one
# of those is a lockfile that looks pinned and does not install.
#
# tag -> commit, and `tests/unit/test_repo_artifacts.py` proves each line against the tag
# `pyproject.toml` names and against the other lockfile:
{tag_map}
"""


def _declared_tags(extra: str) -> dict[str, str]:
    """Each commons package mapped to the tag `pyproject.toml` declares for it.

    Scoped to ONE lockfile: the core dependencies plus the single extra that lockfile is
    compiled with. A commons declared only in a DIFFERENT extra is legitimately absent
    here (a repo may keep `agent-eval-kit` dev-only, because the serving core never
    imports the eval scaffold), and reading every extra at once made that absence look
    like an incomplete header map.
    """
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    requirements = list(data["project"].get("dependencies", []))
    requirements.extend(data["project"].get("optional-dependencies", {}).get(extra, []))
    tags: dict[str, str] = {}
    for requirement in requirements:
        match = re.match(_GIT_PIN, requirement)
        if match:
            tags.setdefault(match.group(1), match.group(2))
    return tags


def _locked_commits(text: str) -> dict[str, str]:
    """Each commons package mapped to the 40-character commit the lockfile body pins."""
    return dict(re.findall(r"^([a-z0-9-]+) @ git\+\S+?@([0-9a-f]{40})", text, re.M))


def _header_for(extra: str, output: str, tags: dict[str, str], commits: dict[str, str]) -> str:
    both = set(tags) & set(commits)
    width = max((len(name) for name in both), default=0)
    lines = [
        f"#   {name.ljust(width)}  {tags[name]} = {commits[name]}" for name in _commons_order(both)
    ]
    return _HEADER.format(extra=extra, output=output, tag_map="\n".join(lines))


def _restore(path: Path, previous: str | None) -> None:
    """Put the file back as it was, because `uv` has already replaced it by this point.

    Every abort below happens AFTER the compile, so without this the script would leave a
    headerless lockfile behind: precisely the damage it exists to prevent, and worse than
    doing nothing because the run also reported failure and looked handled.
    """
    if previous is not None:
        path.write_text(previous, encoding="utf-8")


def main() -> int:
    # Resolve first, once. Without --upgrade this preserves everything `uv.lock` already pins and
    # only picks up what pyproject.toml has actually changed, so `make lock` after adding one
    # dependency moves one dependency. Upgrading is a separate, deliberate `uv lock --upgrade`.
    resolved = subprocess.run(["uv", "lock"], cwd=_REPO_ROOT, check=False)
    if resolved.returncode != 0:
        return resolved.returncode

    for extra, output in _TARGETS:
        tags = _declared_tags(extra)
        path = _REPO_ROOT / output
        previous = path.read_text(encoding="utf-8") if path.exists() else None
        command = [
            "uv",
            "export",
            "--extra",
            extra,
            "--no-hashes",
            "--no-emit-project",
            "-o",
            output,
        ]
        result = subprocess.run(command, cwd=_REPO_ROOT, check=False)
        if result.returncode != 0:
            _restore(path, previous)
            return result.returncode

        raw = path.read_text(encoding="utf-8")
        commits = _locked_commits(raw)
        missing = sorted(set(tags) - set(commits))
        if missing:
            _restore(path, previous)
            print(
                f"{output}: {', '.join(missing)} declared in pyproject.toml but not pinned by "
                "commit in the compiled output; the header map would be incomplete.",
                file=sys.stderr,
            )
            return 1

        # Drop uv's own provenance comment, then re-apply the real header.
        body = "\n".join(line for line in raw.splitlines() if not line.startswith("#")).lstrip("\n")
        path.write_text(_header_for(extra, output, tags, commits) + body + "\n", encoding="utf-8")
        print(f"{output}: compiled, header restored ({len(commits)} commons pinned by commit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
