"""A read that never says `os.environ` is still a read, so delegated reads are scanned too.

`tests/unit/test_three_state_env_reads.py` matches syntax, so a module that hands an env var NAME
to a library function which reads it on the module's behalf is invisible to it: let
`adapters/gcp/_s2s.py` name `S2S_TOKEN` and `S2S_SIGNING_KEY` and pass both to a
`client_headers` whose `os.environ.get(name, "").strip()` collapses the two states, and the
collapse sits inside the commons where no scan here can see it. An emptied credential then
inherits the unset behaviour (no `Authorization` header at all, the call leaving
unauthenticated) with the whole gate green. That is the same blind spot twice removed: a scanner
that only watches one VARIABLE misses a second variable, a scanner that only parses one LANGUAGE
misses the browser tier, and a scanner that only matches one CALL SHAPE misses every delegated
read.

So `TWO_STATE_DELEGATING_READERS` below names the functions that read on a caller's behalf
without resolving three states, and the rule is: any env var name handed to one of them must ALSO
be resolved through `read_env_setting` in the SAME module, so the two unusable states are refused
before the name travels. A module-local rule is deliberate; it keeps the check greppable from the
call site rather than depending on flow analysis across modules.

That registry is EMPTY today, and empty is the goal state rather than a disabled check.
`hex_service_kit.s2s.client_headers` makes its own read three-state and raises on an emptied
credential, so it needs neither an entry here nor a second resolution in `adapters/gcp/_s2s.py`,
because a rule kept in two places is the drift this fleet keeps paying for. The scanner is
proved against a FICTIONAL reader below rather than against whatever the registry happens to
hold, so it stays a working check with nothing registered and is ready for the next library
that reads on a caller's behalf.

This rule lives in its own module because `tests/unit/test_three_state_env_reads.py` is
byte-identical across the whole catalog. A second rule with a second per-repo registry inside
that file makes it a per-repo file again, and drift between the copies stops being visible.
Nothing is lost by keeping them apart: the registry, the scan and its five tests all live here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests import REPO_ROOT
from tests.unit.test_three_state_env_reads import (
    _THREE_STATE_READER,
    _variable_name,
    scanned_sources,
)

#: function name -> (the keyword arguments that carry an env var NAME, why a name handed to it
#: must be resolved three-state in the same module first). These are the library functions that
#: read the environment ON THE CALLER'S BEHALF and do NOT resolve three states while doing it,
#: so the syntactic scan in the sibling module cannot see the read at all. Adding an entry does
#: not grant an exemption: it turns the delegation into a checked one.
#: It is EMPTY because the only candidate reader, hex_service_kit.s2s.client_headers, resolves
#: three states itself; see the module docstring.
TWO_STATE_DELEGATING_READERS: dict[str, tuple[tuple[str, ...], str]] = {}

#: The fictional registry the two proof cases below run against, so the SCANNER is proved rather
#: than whatever happens to be registered today. Its shape is the one that actually shipped.
_PROOF_READERS: dict[str, tuple[tuple[str, ...], str]] = {
    "two_state_client_headers": (
        ("token_env", "signing_key_env"),
        "A fictional library function that does os.environ.get(name, '').strip() and then tests "
        "truthiness, so UNSET and SET-AND-EMPTY are ONE state to it: an emptied credential "
        "attaches no Authorization header and no signed-actor pair, exactly as an unset one "
        "does, and the outbound call leaves unauthenticated with nothing refusing. It exists so "
        "this scanner is still provably able to fail while the real registry is empty.",
    ),
}

#: The proof for the delegated rule, and it is the shape that actually shipped: a module that
#: names a credential, never touches `os.environ`, and hands the name to a reader which cannot
#: tell an emptied value from an absent one.
_DELEGATING_MUTANT = (
    "from fictional_kit.s2s import two_state_client_headers\n"
    'TOKEN_ENV = "SOME_S2S_TOKEN"\n'
    "def headers() -> dict[str, str]:\n"
    "    return two_state_client_headers(token_env=TOKEN_ENV)\n"
)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_function(node: ast.Call) -> str:
    """The bare name of the function a call invokes, however it was imported."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def three_state_resolved_names(tree: ast.AST) -> set[str]:
    """Every env var name this module resolves through the commons' three-state reader."""
    resolved: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _called_function(node) == _THREE_STATE_READER:
            name = _variable_name(node.args[0]) if node.args else None
            if name:
                resolved.add(name)
    return resolved


def delegated_env_names(
    tree: ast.AST, readers: dict[str, tuple[tuple[str, ...], str]] | None = None
) -> list[tuple[int, str, str]]:
    """``(line, function, variable)`` for every env name handed to a two-state reader."""
    registry = TWO_STATE_DELEGATING_READERS if readers is None else readers
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _called_function(node)
        entry = registry.get(called)
        if entry is None:
            continue
        name_keywords = entry[0]
        for keyword in node.keywords:
            if keyword.arg in name_keywords:
                name = _variable_name(keyword.value) or "<computed at runtime>"
                out.append((getattr(node, "lineno", 0), called, name))
    return out


def _delegated_findings(
    path: Path, readers: dict[str, tuple[tuple[str, ...], str]] | None = None
) -> list[tuple[int, str, str]]:
    """Every delegated read in ``path`` whose name this module never resolved three-state."""
    tree = _parse(path)
    resolved = three_state_resolved_names(tree)
    return [
        (line, called, name)
        for line, called, name in delegated_env_names(tree, readers)
        if name not in resolved
    ]


@pytest.mark.parametrize("path", scanned_sources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_module_delegates_an_unresolved_environment_read(path: Path) -> None:
    offenders = [
        f"{path.relative_to(REPO_ROOT)}:{line}: hands {name} to {called}(), which reads it"
        for line, called, name in _delegated_findings(path)
    ]
    assert offenders == [], (
        "these names are handed to a function that reads the environment on this module's "
        "behalf without resolving three states, so an emptied value inherits the unset "
        "behaviour inside a library where the syntactic scan cannot see it. That is how an "
        "emptied outbound credential attached no Authorization header and the call left "
        "unauthenticated with the gate green. Resolve the name with hex_service_kit.netdefaults."
        f"{_THREE_STATE_READER} in this module BEFORE passing it on:\n" + "\n".join(offenders)
    )


def test_the_delegation_scan_actually_finds_an_unresolved_delegation(tmp_path: Path) -> None:
    """The shipped defect, reproduced: a name handed straight to a two-state reader.

    Run against `_PROOF_READERS`, not the live registry, so emptying the registry cannot turn
    this proof into a vacuous pass. A scanner nobody proved can fail is a green tick.
    """
    mutant = tmp_path / "delegating_mutant.py"
    mutant.write_text(_DELEGATING_MUTANT, encoding="utf-8")
    assert _delegated_findings(mutant, _PROOF_READERS) == [
        (4, "two_state_client_headers", "TOKEN_ENV")
    ]


def test_the_delegation_scan_accepts_a_locally_resolved_name(tmp_path: Path) -> None:
    """Resolving the name here first is the fix, so the same call then passes."""
    clean = tmp_path / "delegating_clean.py"
    clean.write_text(
        "from hex_service_kit.netdefaults import read_env_setting\n"
        "from fictional_kit.s2s import two_state_client_headers\n"
        'TOKEN_ENV = "SOME_S2S_TOKEN"\n'
        "def headers() -> dict[str, str]:\n"
        "    setting = read_env_setting(TOKEN_ENV)\n"
        "    if setting.is_configured_empty:\n"
        "        raise RuntimeError(setting.name)\n"
        "    return two_state_client_headers(token_env=TOKEN_ENV)\n",
        encoding="utf-8",
    )
    assert _delegated_findings(clean, _PROOF_READERS) == []


def test_every_delegating_reader_still_matches_a_real_call() -> None:
    """An entry that outlives its call pre-approves the next function of that name."""
    called = {
        _called_function(node)
        for path in scanned_sources()
        for node in ast.walk(_parse(path))
        if isinstance(node, ast.Call)
    }
    stale = sorted(set(TWO_STATE_DELEGATING_READERS) - called)
    assert stale == [], (
        f"{stale} are registered as two-state delegating readers but nothing in the shipped "
        "source calls them any more. Delete the entries; if the commons made the read "
        "three-state, that is the same commit that should remove them."
    )


def test_every_delegating_reader_carries_a_reason() -> None:
    unexplained = sorted(
        name for name, (_, reason) in TWO_STATE_DELEGATING_READERS.items() if len(reason) < 40
    )
    assert unexplained == [], (
        f"{unexplained} are registered as two-state delegating readers with no reason written "
        "down. Say what the function does to the value, so the entry can be retired when it stops."
    )
