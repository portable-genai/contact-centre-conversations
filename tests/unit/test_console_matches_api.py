"""The console calls the API this repository actually serves.

The scaffold's console (`ui/app/page.tsx`) posts the template's own request shape to the template's
own route. A repository that reshapes its API and leaves the console alone ships a console that can
never produce a result: every submit is a 404 or a 422. Nothing else notices. The type-check, the
policy tests, the build and the hydration check all pass over a page that renders, and the API's
own tests call the API directly. That is how consoles across this organization came to post
`{subject, text}` to routes that take an alert id, a claim id or a question.

So every `fetch(API + ...)` in `ui/app/` is read from the source and held against the live route
table of the FastAPI app:

* the path and the method must be served;
* a JSON body must carry every REQUIRED top-level field of the route's request model;
* a JSON body must carry no field the model does not declare. Pydantic drops an undeclared field
  without a word, so a console posting one believes it said something the service never heard.

Only the top level of a body is read: nested shapes are the API's own validation to enforce. A body
the reader cannot see (a spread, or a value built elsewhere) fails rather than passing unread,
because a check that skipped what it could not parse would be green over exactly the drift it is
for. `FormData` bodies are exempt from the field check and still held to path and method.

When you change a route or a request model, change the console call in the same commit; this test
fails until you do. The reader is deliberately small, so write console calls in the plain shape
it reads: `fetch(API + "/v1/route", { method: "POST", body: JSON.stringify({ field, x: y }) })`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from tests import REPO_ROOT

CONSOLE = REPO_ROOT / "ui" / "app"

#: The same-origin proxy prefix every console call is written against. The proxy route itself
#: (`ui/app/api/`) forwards to the service and is not a console call.
API_TOKEN = "API"

_OPEN = "([{"
_CLOSE = ")]}"


@dataclass
class ConsoleCall:
    source: str
    line: int
    method: str
    paths: list[str]
    #: None when there is no body; "form" for a FormData body; otherwise the candidate object
    #: literals (a ternary yields two), each as its set of top-level keys, or the reason it could
    #: not be read.
    bodies: list[set[str]] | str | None = None


# --------------------------------------------------------------------------- #
# A deliberately small reader for the console's call sites
# --------------------------------------------------------------------------- #
def _skip_string(text: str, i: int) -> int:
    """Index just past the string literal opening at ``text[i]``."""
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if quote == "`" and text.startswith("${", i):
            i = _skip_balanced(text, i + 1)
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def _skip_balanced(text: str, i: int) -> int:
    """Index just past the bracket group opening at ``text[i]``."""
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split ``text`` on ``sep`` where it sits outside every bracket and string."""
    parts, start, i, depth = [], 0, 0, 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _literals(expr: str) -> list[tuple[int, int, str]]:
    """Every string literal in ``expr`` outside nested brackets: (start, end, value)."""
    out, i, depth = [], 0, 0
    while i < len(expr):
        ch = expr[i]
        if ch in "\"'`":
            end = _skip_string(expr, i)
            if depth == 0:
                value = re.sub(r"\$\{.*?\}", "{}", expr[i + 1 : end - 1])
                out.append((i, end, value))
            i = end
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        i += 1
    return out


def _paths(target: str) -> list[str]:
    """The route paths the first argument of a console call can name."""
    body = target.strip()
    if not body.startswith(API_TOKEN):
        return []
    body = body[len(API_TOKEN) :].strip().lstrip("+").strip()
    if body.startswith("(") and body.endswith(")"):
        body = body[1:-1].strip()
    lits = _literals(body)
    code = re.sub(r"(\"|'|`)(?:\\.|(?!\1).)*\1", '""', body)
    if "?" in code and ":" in code:
        # `API + (cond ? "/v1/a" : "/v1/b")`: each branch is its own candidate.
        return [value.split("?")[0] for _, _, value in lits if value.startswith("/")]
    path, cursor = "", 0
    for start, end, value in lits:
        if body[cursor:start].strip(" +"):
            path += "{}"
        path += value
        cursor = end
    if body[cursor:].strip(" +"):
        path += "{}"
    return [path.split("?")[0]]


def _object_keys(literal: str) -> set[str] | str:
    """The top-level keys of an object literal, or why they cannot be read."""
    keys: set[str] = set()
    for entry in _split_top(literal.strip()[1:-1]):
        if entry.startswith("..."):
            return "the body spreads " + entry + ", so its fields cannot be read from the source"
        match = re.match(r"""^(?:"([^"]+)"|'([^']+)'|([A-Za-z_$][\w$]*))\s*(?::|$)""", entry)
        if not match:
            return "unreadable body entry: " + entry
        keys.add(next(g for g in match.groups() if g))
    return keys


def _objects(expr: str) -> list[set[str]] | str:
    """The object literals an expression can evaluate to (both branches of a ternary)."""
    found: list[set[str]] = []
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch in "\"'`":
            i = _skip_string(expr, i)
            continue
        if ch == "{":
            end = _skip_balanced(expr, i)
            keys = _object_keys(expr[i:end])
            if isinstance(keys, str):
                return keys
            found.append(keys)
            i = end
            continue
        if ch in "([":
            i = _skip_balanced(expr, i)
            continue
        i += 1
    return found or "no object literal in the body expression: " + expr.strip()


def _initializer(source: str, name: str) -> str | None:
    match = re.search(r"\b(?:const|let|var)\s+" + re.escape(name) + r"\b[^=]*=", source)
    if not match:
        return None
    i, depth, start = match.end(), 0, match.end()
    while i < len(source):
        ch = source[i]
        if ch in "\"'`":
            i = _skip_string(source, i)
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
        elif ch == ";" and depth == 0:
            return source[start:i]
        i += 1
    return source[start:]


def _bodies(options: str, source: str) -> list[set[str]] | str | None:
    match = re.search(r"\bbody\s*:", options)
    if not match:
        return None
    value = options[match.end() :].strip()
    if not value.startswith("JSON.stringify"):
        head = re.match(r"[\w$.]*", value)
        name = head.group(0) if head else value[:20]
        return "form" if "form" in name.lower() else "a body that is not JSON.stringify: " + name
    inner = value[len("JSON.stringify") :].strip()
    inner = inner[1 : _skip_balanced(inner, 0) - 1].strip()
    if inner.startswith("{"):
        return _objects(inner)
    if re.fullmatch(r"[A-Za-z_$][\w$]*", inner):
        init = _initializer(source, inner)
        if init is None:
            return "the body " + inner + " is not declared in this file"
        return _objects(init)
    return "the body is built by an expression the reader does not follow: " + inner


def _without_comments(source: str) -> str:
    """``source`` with every comment blanked, newlines kept so line numbers still point home.

    A comment that SHOWS a call (the scaffold's own wiring note does) is prose, not a call.
    """
    out, i = [], 0
    while i < len(source):
        ch = source[i]
        if ch in "\"'`":
            end = _skip_string(source, i)
            out.append(source[i:end])
            i = end
            continue
        if source.startswith("//", i):
            end = source.find("\n", i)
            end = len(source) if end == -1 else end
            i = end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            end = len(source) if end == -1 else end + 2
            out.append("\n" * source.count("\n", i, end))
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def console_calls(source: str, name: str = "page.tsx") -> list[ConsoleCall]:
    """Every same-origin API call in one console source file."""
    calls: list[ConsoleCall] = []
    source = _without_comments(source)
    for match in re.finditer(r"\bfetch\s*\(", source):
        open_at = match.end() - 1
        args = _split_top(source[open_at + 1 : _skip_balanced(source, open_at) - 1])
        if not args:
            continue
        paths = _paths(args[0])
        if not paths:
            continue
        options = args[1] if len(args) > 1 else ""
        method = re.search(r"""\bmethod\s*:\s*["'](\w+)["']""", options)
        calls.append(
            ConsoleCall(
                source=name,
                line=source.count("\n", 0, match.start()) + 1,
                method=method.group(1).upper() if method else "GET",
                paths=paths,
                bodies=_bodies(options, source),
            )
        )
    return calls


# --------------------------------------------------------------------------- #
# Holding a call against the route table
# --------------------------------------------------------------------------- #
def _route_for(app: FastAPI, method: str, path: str) -> APIRoute | None:
    concrete = path.replace("{}", "x")
    for route in app.routes:
        if (
            isinstance(route, APIRoute)
            and method in route.methods
            and route.path_regex.fullmatch(concrete)
        ):
            return route
    return None


def _request_model(route: APIRoute) -> type[BaseModel] | None:
    params = route.dependant.body_params
    if len(params) != 1:
        return None
    annotation: Any = params[0].field_info.annotation
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def problems(app: FastAPI, calls: list[ConsoleCall]) -> list[str]:
    """Why each call would fail against ``app``; empty when every call is served as written."""
    found: list[str] = []
    for call in calls:
        where = f"ui/app/{call.source}:{call.line}"
        for path in call.paths:
            route = _route_for(app, call.method, path)
            if route is None:
                served = sorted(
                    f"{sorted(r.methods)[0]} {r.path}"
                    for r in app.routes
                    if isinstance(r, APIRoute) and r.path.startswith("/v1/")
                )
                found.append(
                    f"{where}: {call.method} {path} is not served; the API serves {served}"
                )
                continue
            if isinstance(call.bodies, str):
                if call.bodies != "form":
                    found.append(f"{where}: {call.method} {path}: {call.bodies}")
                continue
            model = _request_model(route)
            if call.bodies is None:
                if model is not None:
                    found.append(
                        f"{where}: {call.method} {path} sends no body; it takes {model.__name__}"
                    )
                continue
            if model is None:
                continue
            fields = model.model_fields
            accepted = set(fields) | {f.alias for f in fields.values() if f.alias}
            required = {
                name: {name, info.alias} - {None}
                for name, info in fields.items()
                if info.is_required()
            }
            for keys in call.bodies:
                missing = sorted(n for n, names in required.items() if not keys & names)
                unknown = sorted(keys - accepted)
                if missing:
                    found.append(
                        f"{where}: {call.method} {path} omits required {missing} of "
                        f"{model.__name__}; it sends {sorted(keys)}"
                    )
                if unknown:
                    found.append(
                        f"{where}: {call.method} {path} sends {unknown}, which "
                        f"{model.__name__} does not declare and silently drops"
                    )
    return found


def _console_sources() -> Iterator[tuple[str, str]]:
    for path in sorted(CONSOLE.rglob("*.tsx")):
        relative = path.relative_to(CONSOLE)
        if relative.parts[0] == "api":
            continue
        yield relative.as_posix(), path.read_text(encoding="utf-8")


requires_ui = pytest.mark.skipif(not (CONSOLE / "page.tsx").exists(), reason="no ui/ console")


@requires_ui
def test_every_console_call_is_served_with_the_shape_the_api_takes(api_client: TestClient) -> None:
    calls = [call for name, source in _console_sources() for call in console_calls(source, name)]
    # Non-vacuous: a console whose action the reader cannot see has checked nothing.
    assert any(call.method == "POST" for call in calls), (
        "the reader found no POST from the console to the API in ui/app/; write calls as "
        'fetch(API + "/v1/...", { method: "POST", ... }) so they can be checked'
    )
    app = api_client.app
    assert isinstance(app, FastAPI)
    assert problems(app, calls) == []


# --------------------------------------------------------------------------- #
# The guard, shown red against the defects it exists for
# --------------------------------------------------------------------------- #
class _Model(BaseModel):
    alert_id: str
    note: str = ""


def _mutant_app() -> FastAPI:
    app = FastAPI()

    @app.post("/v1/triage")
    def triage(request: _Model) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    @app.get("/v1/alerts/{alert_id}")
    def one(alert_id: str) -> dict[str, str]:  # pragma: no cover - never called
        return {}

    return app


def _page(call: str) -> str:
    return 'const API = "/api/agent";\nasync function go() {\n  ' + call + "\n}\n"


@pytest.mark.parametrize(
    ("call", "defect"),
    [
        (
            'await fetch(API + "/v1/triage", { method: "POST", '
            "body: JSON.stringify({ subject, text }) });",
            "omits required ['alert_id']",
        ),
        (
            'await fetch(API + "/v1/triage", { method: "POST", '
            "body: JSON.stringify({ alert_id: id, subject }) });",
            "sends ['subject']",
        ),
        (
            'await fetch(API + "/v1/assess", { method: "POST", '
            "body: JSON.stringify({ alert_id }) });",
            "POST /v1/assess is not served",
        ),
        ('await fetch(API + "/v1/triage", { cache: "no-store" });', "GET /v1/triage is not served"),
        (
            'await fetch(API + "/v1/triage", { method: "POST", '
            "body: JSON.stringify({ ...form }) });",
            "spreads",
        ),
        (
            "const body = flag ? { alert_id } : { subject };\n"
            'await fetch(API + (flag ? "/v1/triage" : "/v1/triage"), '
            '{ method: "POST", body: JSON.stringify(body) });',
            "omits required ['alert_id']",
        ),
    ],
)
def test_the_guard_goes_red_on_a_console_that_drifted(call: str, defect: str) -> None:
    found = problems(_mutant_app(), console_calls(_page(call)))
    assert any(defect in problem for problem in found), found


@pytest.mark.parametrize(
    "call",
    [
        'await fetch(API + "/v1/triage", { method: "POST", '
        'headers: { "Content-Type": "application/json" }, '
        "body: JSON.stringify({ alert_id: chosen, note }) });",
        'await fetch(API + "/v1/alerts/" + encodeURIComponent(id), { cache: "no-store" });',
        "await fetch(API + `/v1/alerts/${id}`);",
        # A call a comment only SHOWS is prose; the real call beside it is what gets checked.
        '// fetch(API + "/v1/nope", { method: "POST", body: JSON.stringify({ subject }) })\n'
        '  /* fetch(API + "/v1/nope") */ await fetch(API + "/v1/alerts/" + id);',
    ],
)
def test_the_guard_passes_a_console_that_matches(call: str) -> None:
    calls = console_calls(_page(call))
    assert calls, "the reader did not see the call at all"
    assert problems(_mutant_app(), calls) == []
