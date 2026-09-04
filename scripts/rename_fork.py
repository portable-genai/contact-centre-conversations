"""Rebrand this base repo for your institution or vertical in one pass.

Rewrites the python package name (which is also this repo's console-script name), the
``CONTACT`` environment prefix behind every ``CONTACT_PROFILE``-style variable, the
distribution and resource id ``contact-centre-conversations`` and the Terraform
``name_prefix`` default, so a fork does not have to hunt them down by hand. It is a mechanical
text and directory rename, deliberately conservative: it prints a plan, writes nothing unless
you pass ``--yes``, and by default leaves Markdown prose alone (pass ``--include-docs`` to
sweep that too).

    # See what would change (writes nothing):
    python scripts/rename_fork.py --package acme_contact_centre \
        --env-prefix ACMECONTACT --resource acme-contact-centre \
        --name-prefix acme-contact --dry-run

    # Apply it:
    python scripts/rename_fork.py --package acme_contact_centre \
        --env-prefix ACMECONTACT --resource acme-contact-centre \
        --name-prefix acme-contact --yes

There is deliberately no ``--cli`` flag. This repo's console script is named after the PACKAGE:
the ``[project.scripts]`` entry point in ``pyproject.toml`` is
``contact_centre_conversations``, so ``--package`` renames the CLI as well and a
second flag could only drift out of step with it.

``--resource`` is one literal doing four jobs: the distribution name in ``pyproject.toml``, the
GitHub id in ``[project.urls]``, the A2A agent-card name and the model-quality-gate eval bundle id.
They are the same string on purpose, so a fork's promotion record and its discovery card cannot
disagree about which system they describe.

``--name-prefix`` is the Terraform ``name_prefix`` default that every deployed resource name is
derived from. It is rewritten ONLY inside its own variable block in
``infra/terraform/variables.tf``, and its current value is READ from there rather than
hardcoded here. Both halves matter: it is a short word, and a whole-tree replacement of a short
word is how a rename script corrupts prose it was never asked to touch, while a hardcoded old
value would stop matching the moment somebody renames this repo a second time.

After running: recreate the venv and ``pip install -e ".[dev]"`` (the distribution name
changed), then prove it is green with ``make gate``. See ``docs/ADOPTING.md`` for the human
decisions (region, IdP, policy numbers, fixtures, eval golden set) this script does NOT make
for you.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_OLD_PACKAGE = "contact_centre_conversations"
_OLD_ENV_PREFIX = "CONTACT"
_OLD_RESOURCE = "contact-centre-conversations"

#: Directories never touched. Walking the vendored skill tree would
#: visit the same files twice.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    ".next",
    "out",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
}
#: File suffixes considered text. Everything else is skipped, including images and archives.
#: ``""`` covers the extensionless files that carry identifiers here: ``Dockerfile``,
#: ``Makefile``, ``.env``.
_TEXT_SUFFIXES = {
    ".py",
    ".toml",
    ".yaml",
    ".yml",
    ".md",
    ".json",
    ".jsonl",
    ".cfg",
    ".ini",
    ".txt",
    ".mjs",
    ".ts",
    ".tsx",
    ".lock",
    ".sh",
    ".tf",
    ".tfvars",
    ".hcl",
    ".env",
    ".example",
    "",
}
#: Markdown prose is only swept with ``--include-docs``, so a default run stays reviewable.
_DOC_SUFFIXES = {".md"}

#: The Terraform variable block that carries the resource-name prefix, and nothing else in the
#: tree. Scoped to the block so a short prefix cannot be replaced inside unrelated prose, and
#: matching whatever value is there now so a second rename still works. Group 2 is the value.
_NAME_PREFIX_FILE = Path("infra") / "terraform" / "variables.tf"
_NAME_PREFIX_BLOCK = re.compile(
    r'(variable\s+"name_prefix"\s*\{.*?default\s*=\s*")([a-z][a-z0-9-]*)(")',
    re.DOTALL,
)


def _iter_files(include_docs: bool):
    for path in sorted(_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(_ROOT).parts):
            continue
        if path.suffix not in _TEXT_SUFFIXES:
            continue
        if not include_docs and path.suffix in _DOC_SUFFIXES:
            continue
        yield path


def _replacements(args: argparse.Namespace) -> list[tuple[str, str]]:
    """The whole-tree rules, longest and most specific first.

    The resource id and the package name share no substring (hyphens against underscores), so
    the order between them is cosmetic; the env prefix is last because it is matched by pattern
    rather than by substring and must never see an already-rewritten identifier.
    """
    return [
        (_OLD_RESOURCE, args.resource),
        (_OLD_PACKAGE, args.package),
        (_OLD_ENV_PREFIX, args.env_prefix),
    ]


def _rewrite_text(text: str, repls: list[tuple[str, str]]) -> tuple[str, int]:
    count = 0
    for old, new in repls:
        if old == _OLD_ENV_PREFIX:
            # Only where it IS the environment prefix: ``CONTACT_NAME``, the
            # ``CONTACT_*`` glob a comment writes, and the bare token in
            # infra/terraform/render.tf.json. Never mid-word, so a longer identifier that
            # merely starts with the prefix is left alone.
            text, n = re.subn(rf"\b{re.escape(old)}(?=_[A-Z0-9*]|[^A-Za-z0-9_]|$)", new, text)
        else:
            n = text.count(old)
            text = text.replace(old, new)
        count += n
    return text, count


def _rewrite_name_prefix(new_prefix: str, apply: bool) -> str | None:
    """Rewrite the Terraform name_prefix default inside its own variable block only.

    Returns the value that was there, or ``None`` when the block could not be found. The value
    is spliced in by slice rather than by a regex backreference, so no character in either the
    old value or the new one can be read as a replacement escape.
    """
    path = _ROOT / _NAME_PREFIX_FILE
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = _NAME_PREFIX_BLOCK.search(text)
    if match is None:
        return None
    if apply:
        rewritten = text[: match.start(2)] + new_prefix + text[match.end(2) :]
        path.write_text(rewritten, encoding="utf-8")
    return match.group(2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebrand the base repo for a fork.")
    ap.add_argument(
        "--package",
        required=True,
        help="new python package name, snake_case; also the console-script name",
    )
    ap.add_argument("--env-prefix", required=True, help="new env-var prefix, e.g. ACMECONTACT")
    ap.add_argument(
        "--resource",
        required=True,
        help="new distribution / resource id, kebab-case, e.g. acme-contact-centre",
    )
    ap.add_argument(
        "--name-prefix",
        default="",
        help="new Terraform name_prefix default, e.g. acme-contact (default: leave it alone)",
    )
    ap.add_argument("--include-docs", action="store_true", help="also rewrite Markdown prose")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--yes", action="store_true", help="apply without the confirmation prompt")
    args = ap.parse_args()

    if not re.fullmatch(r"[a-z_][a-z0-9_]*", args.package):
        ap.error("--package must be a valid snake_case python identifier")
    args.env_prefix = args.env_prefix.strip().rstrip("_").upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", args.env_prefix):
        ap.error("--env-prefix must be an UPPER_SNAKE token, e.g. ACMECONTACT")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", args.resource):
        ap.error("--resource must be a kebab-case id, e.g. acme-contact-centre")
    if args.name_prefix and not re.fullmatch(r"[a-z][a-z0-9-]{2,18}", args.name_prefix):
        # The same rule infra/terraform/variables.tf enforces, applied here so the mistake is
        # caught at rename time rather than at somebody's first `terraform plan`.
        ap.error("--name-prefix must match ^[a-z][a-z0-9-]{2,18}$, e.g. acme-contact")

    # Write only when explicitly applying (--yes and not --dry-run); otherwise preview.
    apply = args.yes and not args.dry_run

    repls = _replacements(args)
    print("Planned replacements (whole-tree):")
    for old, new in repls:
        print(f"  {old!r:34} -> {new!r}")
    if args.name_prefix:
        current = _rewrite_name_prefix(args.name_prefix, apply=False) or "(not found)"
        print(f"  {current!r:34} -> {args.name_prefix!r}  ({_NAME_PREFIX_FILE} only)")
    print()

    touched: list[tuple[Path, int]] = []
    for path in _iter_files(args.include_docs):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new_text, n = _rewrite_text(text, repls)
        if n:
            touched.append((path, n))
            if apply:
                path.write_text(new_text, encoding="utf-8")

    total = sum(n for _, n in touched)
    verb = "Edited" if apply else "Would edit"
    print(f"{verb} {len(touched)} file(s), {total} replacement(s).")

    if args.name_prefix:
        was = _rewrite_name_prefix(args.name_prefix, apply)
        if was is None:
            print(
                f"WARNING: no name_prefix default found in {_NAME_PREFIX_FILE}; "
                "set the Terraform resource prefix by hand."
            )
        else:
            print(f"{verb} the name_prefix default {was!r} in {_NAME_PREFIX_FILE}.")

    # Rename the package directory last, after the contents referring to it are rewritten.
    pkg_dir = _ROOT / "src" / _OLD_PACKAGE
    new_pkg_dir = _ROOT / "src" / args.package
    if pkg_dir.exists() and pkg_dir != new_pkg_dir:
        print(f"{'Renaming' if apply else 'Would rename'} {pkg_dir} -> {new_pkg_dir}")
        if apply:
            pkg_dir.rename(new_pkg_dir)

    if not apply:
        print("\nNo files were written. Re-run with --yes to apply (or --dry-run to preview).")
        return 0
    print('\nDone. Next: recreate the venv, `pip install -e ".[dev]"`, and run `make gate`.')
    print("Then finish the human steps in docs/ADOPTING.md (region, IdP, policy, fixtures, eval).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
