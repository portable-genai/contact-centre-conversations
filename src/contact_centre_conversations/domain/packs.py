"""Procedure, disclosure, allowlist and action packs: the bank's policy, as DATA.

Nothing in this repository decides what an agent must say, in which order, or what a customer
may ask a machine to do. Those are the client's reviewed policy, they differ per market and per
tenant, and they change on a compliance timetable rather than a release timetable. So they are
packs: validated data, loaded from ``config/packs/``, and the engines are pure functions over
them. Changing a required disclosure is a pack edit and a pack-schema check, never a code
change and never a release of this service.

Four pack kinds, one loader:

* **procedure** drives the next-best-step engine: states, entry and exit criteria, allowed
  transitions, required evidence, and the lexicon whose phrase hits advance the state.
* **disclosure** drives the reminder engine: required phrase, accepted paraphrases, trigger
  event, timing window, jurisdiction and severity. **This shape is deliberately shared with
  E3** (`conversation-qa-scorecard`), so a market's disclosure pack is portable between the
  live copilot that reminds an agent and the post-contact scorecard that grades whether the
  reminder worked. Two engines reading one reviewed artifact is the point; two artifacts that
  drift is the failure.
* **allowlist** drives the self-service policy gate: the intents a machine may handle and,
  separately, the actions it may take, per tenant and per market. An EMPTY allowlist admits
  nobody, which is asserted here rather than left to the caller.
* **actions** is the tool catalog's metadata: parameter schema, and the ``consequential`` flag
  that stops an action from ever auto-executing.
* **cues** carries the two customer-side phrase lists the handoff triggers read: an explicit
  request for a person, and vulnerability signals. They are policy (a bank decides what counts
  as a vulnerability cue in its market) and they are per market, so they are a pack rather than
  a constant somebody edits in Python.

Every pack declares a ``vertical``, the line of business whose reviewed policy it carries, and
packs are selected by ``(market, vertical)`` rather than by market alone. A bank and an insurer
both operate in SG and their procedures, disclosures, cues and action catalogs are different
artifacts reviewed by different people. Keying on market alone made two such packs load, both
validate, and one silently win by whichever filename sorted first, which is a compliance
artifact being chosen by alphabetical accident. Two packs claiming the same key is now a load
failure, checked in :meth:`PackLibrary.check_cross_references`.

This module is PURE. It parses and validates mappings that somebody else read off a disk;
``config.py`` is the only place that opens a file. That split is what lets the whole pack
vocabulary be unit-tested with no filesystem and no fixtures directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from speech_lexicon_kit import ChannelRole, Lexicon, LexiconEntry, MatchMode, PhraseSpec

from .kernel import Severity
from .models import ActionSpec, ParameterSpec

__all__ = [
    "ActionCatalogPack",
    "AllowlistPack",
    "CuePack",
    "DisclosurePack",
    "DisclosureSpec",
    "IntentSpec",
    "PackError",
    "PackLibrary",
    "ProcedurePack",
    "ProcedureState",
    "TRIGGER_CONTACT_START",
    "parse_pack",
]

#: The one trigger that needs no evidence: the contact existing at all.
TRIGGER_CONTACT_START = "contact_start"
#: A trigger naming a procedure state: the window opens when that state is entered.
_TRIGGER_STATE = "procedure_state:"
#: A trigger naming a lexicon entry: the window opens at the first hit on that entry.
_TRIGGER_LEXICON = "lexicon:"

_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


class PackError(ValueError):
    """A pack is missing a required field, names something that does not exist, or is empty.

    Every failure here is a BOOT failure by the time it matters: packs load with the settings,
    so a pack that cannot be validated stops the process rather than producing a service that
    silently has no procedure, no disclosures or, worst of all, an empty allowlist it treats as
    permission.
    """


def _mapping(node: Any, what: str) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise PackError(f"{what} must be a mapping, got {type(node).__name__}")
    return node


def _text(node: Mapping[str, Any], key: str, what: str, *, required: bool = True) -> str:
    value = str(node.get(key) or "").strip()
    if required and not value:
        raise PackError(f"{what}: '{key}' is required and must not be empty")
    return value


def _identifier(node: Mapping[str, Any], key: str, what: str) -> str:
    value = _text(node, key, what)
    if not _ID.match(value):
        raise PackError(
            f"{what}: '{key}'={value!r} is not an identifier "
            "(lowercase letters, digits, dot, dash and underscore; not starting with punctuation)"
        )
    return value


def _strings(node: Mapping[str, Any], key: str, what: str) -> tuple[str, ...]:
    raw = node.get(key) or ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise PackError(f"{what}: '{key}' must be a list of strings")
    out = tuple(str(item).strip() for item in raw)
    if any(not item for item in out):
        raise PackError(f"{what}: '{key}' contains an empty entry")
    return out


def _positive_int(node: Mapping[str, Any], key: str, what: str) -> int | None:
    raw = node.get(key)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise PackError(f"{what}: '{key}' must be a whole number of milliseconds") from exc
    if value <= 0:
        raise PackError(f"{what}: '{key}' must be positive, got {value}")
    return value


def _fraction(node: Mapping[str, Any], key: str, what: str, *, default: float) -> float:
    raw = node.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise PackError(f"{what}: '{key}' must be a number between 0 and 1") from exc
    if not 0.0 <= value <= 1.0:
        raise PackError(f"{what}: '{key}'={value} is outside 0..1")
    return value


def _severity(node: Mapping[str, Any], what: str) -> Severity:
    raw = str(node.get("severity") or "").strip()
    if not raw:
        raise PackError(f"{what}: 'severity' is required (a missing severity is not a low one)")
    try:
        return Severity(raw)
    except ValueError as exc:
        known = [s.value for s in Severity]
        raise PackError(f"{what}: severity {raw!r} is not one of {known}") from exc


def _role(node: Mapping[str, Any], what: str, *, default: ChannelRole) -> ChannelRole:
    raw = str(node.get("role") or "").strip()
    if not raw:
        return default
    try:
        role = ChannelRole(raw)
    except ValueError as exc:  # pragma: no cover - ChannelRole is lenient; kept for clarity
        raise PackError(f"{what}: role {raw!r} is not a channel role") from exc
    if role is ChannelRole.UNKNOWN:
        raise PackError(f"{what}: role {raw!r} is not a channel role")
    return role


def _lexicon(node: Mapping[str, Any], *, lexicon_id: str, locale: str, what: str) -> Lexicon:
    raw_entries = node.get("lexicon") or ()
    if isinstance(raw_entries, str) or not isinstance(raw_entries, Sequence):
        raise PackError(f"{what}: 'lexicon' must be a list of entries")
    entries: list[LexiconEntry] = []
    for item in raw_entries:
        entry = _mapping(item, f"{what}: lexicon entry")
        entry_id = _identifier(entry, "entry_id", f"{what}: lexicon entry")
        phrases = _strings(entry, "phrases", f"{what}: lexicon entry {entry_id}")
        if not phrases:
            raise PackError(f"{what}: lexicon entry {entry_id} has no phrases")
        entries.append(
            LexiconEntry(
                entry_id=entry_id,
                phrases=tuple(
                    PhraseSpec(
                        phrase_id=f"{entry_id}#{position}",
                        text=phrase,
                        mode=MatchMode.CONTIGUOUS,
                    )
                    for position, phrase in enumerate(phrases)
                ),
                tags=_strings(entry, "tags", f"{what}: lexicon entry {entry_id}"),
            )
        )
    if not entries:
        raise PackError(f"{what}: 'lexicon' is empty, so no phrase can ever be evidenced")
    return Lexicon(lexicon_id=lexicon_id, locale=locale, entries=tuple(entries))


# --------------------------------------------------------------------------------------- #
# Procedure packs
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ProcedureState:
    """One state of a procedure: what it asks for, and where it may go next."""

    state_id: str
    title: str
    instruction: str
    #: Lexicon entry ids that must ALL be hit, in this order, before the state may be left.
    exit_criteria: tuple[str, ...]
    #: What a reviewer must be able to see happened in this state. A superset of exit criteria.
    required_evidence: tuple[str, ...]
    #: The states this one may advance to. A transition not named here cannot be taken.
    transitions: tuple[str, ...]
    #: Which speaker's turns count as evidence for this state.
    role: ChannelRole = ChannelRole.AGENT


@dataclass(frozen=True, slots=True)
class ProcedurePack:
    """A whole procedure, as reviewed policy: states, transitions and the evidence lexicon."""

    pack_id: str
    market: str
    vertical: str
    locale: str
    initial_state: str
    states: tuple[ProcedureState, ...]
    lexicon: Lexicon

    @property
    def key(self) -> tuple[str, str]:
        return (self.market, self.vertical)

    def state(self, state_id: str) -> ProcedureState:
        for candidate in self.states:
            if candidate.state_id == state_id:
                return candidate
        raise PackError(f"procedure pack {self.pack_id}: no state {state_id!r}")

    @property
    def state_ids(self) -> tuple[str, ...]:
        return tuple(state.state_id for state in self.states)


def _procedure(node: Mapping[str, Any]) -> ProcedurePack:
    what = "procedure pack"
    pack_id = _identifier(node, "pack_id", what)
    what = f"procedure pack {pack_id}"
    locale = _text(node, "locale", what)
    states: list[ProcedureState] = []
    raw_states = node.get("states") or ()
    if isinstance(raw_states, str) or not isinstance(raw_states, Sequence):
        raise PackError(f"{what}: 'states' must be a list")
    for item in raw_states:
        entry = _mapping(item, f"{what}: state")
        state_id = _identifier(entry, "state_id", f"{what}: state")
        states.append(
            ProcedureState(
                state_id=state_id,
                title=_text(entry, "title", f"{what}: state {state_id}"),
                instruction=_text(entry, "instruction", f"{what}: state {state_id}"),
                exit_criteria=_strings(entry, "exit_criteria", f"{what}: state {state_id}"),
                required_evidence=_strings(entry, "required_evidence", f"{what}: state {state_id}"),
                transitions=_strings(entry, "transitions", f"{what}: state {state_id}"),
                role=_role(entry, f"{what}: state {state_id}", default=ChannelRole.AGENT),
            )
        )
    if not states:
        raise PackError(f"{what}: a procedure with no states can never emit a next best step")
    lexicon = _lexicon(node, lexicon_id=f"{pack_id}-lexicon", locale=locale, what=what)
    pack = ProcedurePack(
        pack_id=pack_id,
        market=_text(node, "market", what),
        vertical=_identifier(node, "vertical", what),
        locale=locale,
        initial_state=_identifier(node, "initial_state", what),
        states=tuple(states),
        lexicon=lexicon,
    )
    _check_procedure_references(pack)
    return pack


def _check_procedure_references(pack: ProcedurePack) -> None:
    """Every id a state names must exist. A dangling reference is a silently dead branch."""
    known_states = set(pack.state_ids)
    known_entries = set(pack.lexicon.entry_ids)
    if pack.initial_state not in known_states:
        raise PackError(
            f"procedure pack {pack.pack_id}: initial_state {pack.initial_state!r} is not a state"
        )
    if len(known_states) != len(pack.states):
        raise PackError(f"procedure pack {pack.pack_id}: duplicate state ids")
    for state in pack.states:
        for target in state.transitions:
            if target not in known_states:
                raise PackError(
                    f"procedure pack {pack.pack_id}: state {state.state_id!r} may transition to "
                    f"{target!r}, which is not a state in this pack"
                )
        for entry_id in (*state.exit_criteria, *state.required_evidence):
            if entry_id not in known_entries:
                raise PackError(
                    f"procedure pack {pack.pack_id}: state {state.state_id!r} requires evidence "
                    f"{entry_id!r}, which is not a lexicon entry in this pack"
                )


# --------------------------------------------------------------------------------------- #
# Disclosure packs (the shape shared with E3)
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class DisclosureSpec:
    """One required disclosure: the wording, when it becomes due, and how long the window is."""

    disclosure_id: str
    required_phrase: str
    paraphrases: tuple[str, ...]
    trigger_event: str
    severity: Severity
    reminder: str
    #: Milliseconds from the trigger within which the disclosure must be made. None means the
    #: contact end is the deadline, which is still a deadline.
    within_ms: int | None = None
    role: ChannelRole = ChannelRole.AGENT

    @property
    def phrases(self) -> tuple[str, ...]:
        """The required phrase plus every accepted paraphrase, in pack order."""
        return (self.required_phrase, *self.paraphrases)


@dataclass(frozen=True, slots=True)
class DisclosurePack:
    """Every disclosure one market requires, plus the lexicon compiled from their phrasings."""

    pack_id: str
    market: str
    vertical: str
    jurisdiction: str
    locale: str
    disclosures: tuple[DisclosureSpec, ...]
    lexicon: Lexicon

    @property
    def key(self) -> tuple[str, str]:
        return (self.market, self.vertical)

    def spec(self, disclosure_id: str) -> DisclosureSpec:
        for candidate in self.disclosures:
            if candidate.disclosure_id == disclosure_id:
                return candidate
        raise PackError(f"disclosure pack {self.pack_id}: no disclosure {disclosure_id!r}")


def _disclosure(node: Mapping[str, Any]) -> DisclosurePack:
    what = "disclosure pack"
    pack_id = _identifier(node, "pack_id", what)
    what = f"disclosure pack {pack_id}"
    locale = _text(node, "locale", what)
    raw = node.get("disclosures") or ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise PackError(f"{what}: 'disclosures' must be a list")
    specs: list[DisclosureSpec] = []
    entries: list[LexiconEntry] = []
    for item in raw:
        entry = _mapping(item, f"{what}: disclosure")
        disclosure_id = _identifier(entry, "disclosure_id", f"{what}: disclosure")
        scope = f"{what}: disclosure {disclosure_id}"
        spec = DisclosureSpec(
            disclosure_id=disclosure_id,
            required_phrase=_text(entry, "required_phrase", scope),
            paraphrases=_strings(entry, "paraphrases", scope),
            trigger_event=_text(entry, "trigger_event", scope),
            severity=_severity(entry, scope),
            reminder=_text(entry, "reminder", scope),
            within_ms=_positive_int(entry, "within_ms", scope),
            role=_role(entry, scope, default=ChannelRole.AGENT),
        )
        specs.append(spec)
        entries.append(
            LexiconEntry(
                entry_id=disclosure_id,
                phrases=tuple(
                    PhraseSpec(
                        phrase_id=f"{disclosure_id}#{position}",
                        text=phrase,
                        mode=MatchMode.CONTIGUOUS,
                    )
                    for position, phrase in enumerate(spec.phrases)
                ),
            )
        )
    if not specs:
        raise PackError(f"{what}: a disclosure pack with no disclosures evidences nothing")
    if len({s.disclosure_id for s in specs}) != len(specs):
        raise PackError(f"{what}: duplicate disclosure ids")
    return DisclosurePack(
        pack_id=pack_id,
        market=_text(node, "market", what),
        vertical=_identifier(node, "vertical", what),
        jurisdiction=_text(node, "jurisdiction", what),
        locale=locale,
        disclosures=tuple(specs),
        lexicon=Lexicon(lexicon_id=f"{pack_id}-lexicon", locale=locale, entries=tuple(entries)),
    )


# --------------------------------------------------------------------------------------- #
# Allowlist packs (the self-service gate's whole vocabulary)
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class IntentSpec:
    """One intent a machine is permitted to handle, and the actions it may reach."""

    intent_id: str
    title: str
    confidence_floor: float
    #: The action ids this intent may request. An intent with none may only answer.
    actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AllowlistPack:
    """The fail-closed allowlists for one tenant in one market.

    Two allowlists, deliberately separate: naming an intent the assistant may HANDLE is not the
    same permission as naming an action it may TAKE, and a single list conflates a question with
    a transaction. Either being empty refuses everything, which is checked before anything else
    happens in ``policy_gate``.
    """

    tenant: str
    market: str
    vertical: str
    locale: str
    intents: tuple[IntentSpec, ...]
    allowed_actions: tuple[str, ...]
    #: None exactly when there are no intents: an empty allowlist has no phrase list at all.
    lexicon: Lexicon | None
    #: Applied to any intent that names no floor of its own.
    default_confidence_floor: float = 0.6

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant, self.market, self.vertical)

    @property
    def intent_ids(self) -> tuple[str, ...]:
        return tuple(intent.intent_id for intent in self.intents)

    def intent(self, intent_id: str) -> IntentSpec | None:
        for candidate in self.intents:
            if candidate.intent_id == intent_id:
                return candidate
        return None


def _allowlist(node: Mapping[str, Any]) -> AllowlistPack:
    what = "allowlist pack"
    tenant = _identifier(node, "tenant", what)
    market = _text(node, "market", what)
    vertical = _identifier(node, "vertical", what)
    what = f"allowlist pack {tenant}/{market}/{vertical}"
    locale = _text(node, "locale", what)
    default_floor = _fraction(node, "confidence_floor", what, default=0.6)
    raw = node.get("intents") or ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise PackError(f"{what}: 'intents' must be a list")
    intents: list[IntentSpec] = []
    entries: list[LexiconEntry] = []
    for item in raw:
        entry = _mapping(item, f"{what}: intent")
        intent_id = _identifier(entry, "intent_id", f"{what}: intent")
        scope = f"{what}: intent {intent_id}"
        phrases = _strings(entry, "phrases", scope)
        if not phrases:
            raise PackError(f"{scope}: an intent with no phrases can never be matched")
        intents.append(
            IntentSpec(
                intent_id=intent_id,
                title=_text(entry, "title", scope),
                confidence_floor=_fraction(entry, "confidence_floor", scope, default=default_floor),
                actions=_strings(entry, "actions", scope),
            )
        )
        entries.append(
            LexiconEntry(
                entry_id=intent_id,
                phrases=tuple(
                    PhraseSpec(
                        phrase_id=f"{intent_id}#{position}",
                        text=phrase,
                        mode=MatchMode.CONTIGUOUS,
                    )
                    for position, phrase in enumerate(phrases)
                ),
            )
        )
    # An EMPTY allowlist is representable on purpose: it is the fail-closed state, and
    # `policy_gate` refuses on it before anything else. What is NOT representable is a wildcard,
    # and an empty pack carries NO lexicon rather than a lexicon that matches nothing: a phrase
    # list nobody wrote and a phrase list that happens to miss are different facts.
    lexicon = (
        Lexicon(
            lexicon_id=f"{tenant}-{market}-{vertical}-intents",
            locale=locale,
            entries=tuple(entries),
        )
        if entries
        else None
    )
    return AllowlistPack(
        tenant=tenant,
        market=market,
        vertical=vertical,
        locale=locale,
        intents=tuple(intents),
        allowed_actions=_strings(node, "actions", what),
        lexicon=lexicon,
        default_confidence_floor=default_floor,
    )


# --------------------------------------------------------------------------------------- #
# Action catalog packs
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class ActionCatalogPack:
    """The action catalog's metadata: the parameter schema and the consequential flag."""

    catalog_id: str
    vertical: str
    actions: tuple[ActionSpec, ...]

    def spec(self, action_id: str) -> ActionSpec | None:
        for candidate in self.actions:
            if candidate.action_id == action_id:
                return candidate
        return None

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(action.action_id for action in self.actions)


def _parameter(node: Any, scope: str) -> ParameterSpec:
    """One declared parameter. ``binds_to_party`` is required, like ``consequential`` above.

    A parameter whose catalog forgot to say whether it names somebody's record is exactly the
    parameter nobody thought about, and defaulting it to "does not" would leave it unchecked
    while looking deliberate.
    """
    param = _mapping(node, f"{scope}: parameter")
    name = _text(param, "name", f"{scope}: parameter")
    if "binds_to_party" not in param:
        raise PackError(
            f"{scope}: parameter {name!r} must say 'binds_to_party'. A value that names a "
            "record somebody owns has to be checked against who is asking, and a parameter "
            "nobody classified is the one that gets missed."
        )
    return ParameterSpec(
        name=name,
        kind=str(param.get("kind") or "string"),
        required=bool(param.get("required", True)),
        pattern=str(param.get("pattern") or ""),
        binds_to_party=bool(param.get("binds_to_party", False)),
    )


def _actions(node: Mapping[str, Any]) -> ActionCatalogPack:
    what = "action catalog"
    catalog_id = _identifier(node, "catalog_id", what)
    what = f"action catalog {catalog_id}"
    raw = node.get("actions") or ()
    if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise PackError(f"{what}: 'actions' must be a list")
    specs: list[ActionSpec] = []
    for item in raw:
        entry = _mapping(item, f"{what}: action")
        action_id = _identifier(entry, "action_id", f"{what}: action")
        scope = f"{what}: action {action_id}"
        raw_params = entry.get("parameters") or ()
        if isinstance(raw_params, str) or not isinstance(raw_params, Sequence):
            raise PackError(f"{scope}: 'parameters' must be a list")
        parameters = tuple(_parameter(param, scope) for param in raw_params)
        if "consequential" not in entry:
            raise PackError(
                f"{scope}: 'consequential' is required. An action whose catalog forgot to say "
                "is treated as consequential, and a silent default is how that gets forgotten."
            )
        specs.append(
            ActionSpec(
                action_id=action_id,
                title=_text(entry, "title", scope),
                consequential=bool(entry.get("consequential", True)),
                parameters=parameters,
                severity=_severity(entry, scope),
            )
        )
    if len({s.action_id for s in specs}) != len(specs):
        raise PackError(f"{what}: duplicate action ids")
    return ActionCatalogPack(
        catalog_id=catalog_id,
        vertical=_identifier(node, "vertical", what),
        actions=tuple(specs),
    )


# --------------------------------------------------------------------------------------- #
# Cue packs
# --------------------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class CuePack:
    """The two customer-side cue lists the deterministic handoff triggers read.

    Separate lexicons rather than one with tags, because they mean different things and produce
    different handoff reasons: "the customer asked for a person" is what the customer sees on
    their transfer, and "a vulnerability cue matched" is what the receiving agent needs to know
    before they open their mouth.
    """

    pack_id: str
    market: str
    vertical: str
    locale: str
    escalation: Lexicon | None
    vulnerability: Lexicon | None

    @property
    def key(self) -> tuple[str, str]:
        return (self.market, self.vertical)


def _cue_lexicon(
    phrases: tuple[str, ...], *, lexicon_id: str, locale: str, entry_id: str
) -> Lexicon | None:
    if not phrases:
        return None
    return Lexicon(
        lexicon_id=lexicon_id,
        locale=locale,
        entries=(
            LexiconEntry(
                entry_id=entry_id,
                phrases=tuple(
                    PhraseSpec(
                        phrase_id=f"{entry_id}#{position}",
                        text=phrase,
                        mode=MatchMode.CONTIGUOUS,
                    )
                    for position, phrase in enumerate(phrases)
                ),
            ),
        ),
    )


def _cues(node: Mapping[str, Any]) -> CuePack:
    what = "cue pack"
    pack_id = _identifier(node, "pack_id", what)
    what = f"cue pack {pack_id}"
    locale = _text(node, "locale", what)
    escalation = _strings(node, "escalation", what)
    vulnerability = _strings(node, "vulnerability", what)
    if not escalation and not vulnerability:
        raise PackError(f"{what}: a cue pack with neither list triggers nothing")
    return CuePack(
        pack_id=pack_id,
        market=_text(node, "market", what),
        vertical=_identifier(node, "vertical", what),
        locale=locale,
        escalation=_cue_lexicon(
            escalation, lexicon_id=f"{pack_id}-escalation", locale=locale, entry_id="escalation"
        ),
        vulnerability=_cue_lexicon(
            vulnerability,
            lexicon_id=f"{pack_id}-vulnerability",
            locale=locale,
            entry_id="vulnerability",
        ),
    )


# --------------------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------------------- #
_PARSERS = {
    "procedure": _procedure,
    "disclosure": _disclosure,
    "allowlist": _allowlist,
    "actions": _actions,
    "cues": _cues,
}


def parse_pack(node: Mapping[str, Any]) -> Any:
    """Parse one pack document, dispatching on its ``kind``."""
    data = _mapping(node, "pack")
    kind = str(data.get("kind") or "").strip()
    parser = _PARSERS.get(kind)
    if parser is None:
        raise PackError(f"pack 'kind'={kind!r} is not one of {sorted(_PARSERS)}")
    return parser(data)


@dataclass(frozen=True, slots=True)
class PackLibrary:
    """Every pack this deployment loaded, indexed the way the engines ask for them.

    The EMPTY library is the fail-closed state and is perfectly representable: a service that
    loaded no packs has no procedure to advance, no disclosure to remind about and no intent
    anyone may ask for. Each engine refuses accordingly rather than inventing a default.
    """

    procedures: tuple[ProcedurePack, ...] = ()
    disclosures: tuple[DisclosurePack, ...] = ()
    allowlists: tuple[AllowlistPack, ...] = ()
    catalogs: tuple[ActionCatalogPack, ...] = ()
    cues: tuple[CuePack, ...] = ()

    @classmethod
    def empty(cls) -> PackLibrary:
        return cls()

    @classmethod
    def from_documents(cls, documents: Iterable[Mapping[str, Any]]) -> PackLibrary:
        procedures: list[ProcedurePack] = []
        disclosures: list[DisclosurePack] = []
        allowlists: list[AllowlistPack] = []
        catalogs: list[ActionCatalogPack] = []
        cues: list[CuePack] = []
        for document in documents:
            pack = parse_pack(document)
            if isinstance(pack, ProcedurePack):
                procedures.append(pack)
            elif isinstance(pack, DisclosurePack):
                disclosures.append(pack)
            elif isinstance(pack, AllowlistPack):
                allowlists.append(pack)
            elif isinstance(pack, CuePack):
                cues.append(pack)
            else:
                catalogs.append(pack)
        library = cls(
            procedures=tuple(procedures),
            disclosures=tuple(disclosures),
            allowlists=tuple(allowlists),
            catalogs=tuple(catalogs),
            cues=tuple(cues),
        )
        library.check_cross_references()
        return library

    def procedure_for(self, market: str, vertical: str) -> ProcedurePack | None:
        return next((p for p in self.procedures if p.key == (market, vertical)), None)

    def disclosure_for(self, market: str, vertical: str) -> DisclosurePack | None:
        return next((p for p in self.disclosures if p.key == (market, vertical)), None)

    def cues_for(self, market: str, vertical: str) -> CuePack | None:
        return next((p for p in self.cues if p.key == (market, vertical)), None)

    def allowlist_for(self, tenant: str, market: str, vertical: str) -> AllowlistPack | None:
        return next((p for p in self.allowlists if p.key == (tenant, market, vertical)), None)

    def action_spec(self, action_id: str, vertical: str) -> ActionSpec | None:
        """The action as THIS vertical's catalog declares it.

        Scoped rather than global: two lines of business may both declare ``cancel_policy`` and
        mean different things by it, with different parameters and a different consequential
        flag. A flat namespace would let an insurer's catalog answer a banking contact.
        """
        for catalog in self.catalogs:
            if catalog.vertical != vertical:
                continue
            found = catalog.spec(action_id)
            if found is not None:
                return found
        return None

    def action_ids_for(self, vertical: str) -> tuple[str, ...]:
        matching = (c for c in self.catalogs if c.vertical == vertical)
        return tuple(sorted({a for catalog in matching for a in catalog.action_ids}))

    @property
    def action_ids(self) -> tuple[str, ...]:
        return tuple(sorted({a for catalog in self.catalogs for a in catalog.action_ids}))

    def check_cross_references(self) -> None:
        """Every id one pack names in another pack must exist. Checked once, at load.

        A disclosure whose trigger names a procedure state that was renamed becomes a window
        that never opens, which looks exactly like a market with no disclosure requirement.
        """
        self._check_unique_keys()
        for allowlist in self.allowlists:
            catalog_actions = set(self.action_ids_for(allowlist.vertical))
            scope = f"allowlist {allowlist.tenant}/{allowlist.market}/{allowlist.vertical}"
            for intent in allowlist.intents:
                for action_id in intent.actions:
                    if action_id not in catalog_actions:
                        raise PackError(
                            f"{scope}: intent {intent.intent_id!r} names action {action_id!r}, "
                            f"which no {allowlist.vertical} action catalog declares"
                        )
            for action_id in allowlist.allowed_actions:
                if action_id not in catalog_actions:
                    raise PackError(
                        f"{scope}: allowed action {action_id!r} is not declared by any "
                        f"{allowlist.vertical} action catalog"
                    )
        for pack in self.disclosures:
            procedure = self.procedure_for(pack.market, pack.vertical)
            for spec in pack.disclosures:
                self._check_trigger(pack, spec, procedure)

    def _check_unique_keys(self) -> None:
        """Two packs of one kind claiming one key is a load failure, not a race to sort first.

        Before the key carried a vertical, a second SG procedure pack loaded, validated, and
        then lost silently to whichever filename sorted earlier: the service ran on a
        compliance artifact chosen by alphabetical accident, and nothing anywhere said so.
        Refusing here means the failure is a boot error naming both packs.
        """
        claims: list[tuple[str, tuple[str, ...], str]] = []
        for procedure in self.procedures:
            claims.append(("procedure", procedure.key, procedure.pack_id))
        for disclosure in self.disclosures:
            claims.append(("disclosure", disclosure.key, disclosure.pack_id))
        for cue in self.cues:
            claims.append(("cue", cue.key, cue.pack_id))
        for allowlist in self.allowlists:
            claims.append(("allowlist", allowlist.key, f"{allowlist.tenant}/{allowlist.market}"))
        for catalog in self.catalogs:
            claims.append(("action catalog", (catalog.vertical,), catalog.catalog_id))

        seen: dict[tuple[str, tuple[str, ...]], str] = {}
        for label, identity, name in claims:
            if (label, identity) in seen:
                raise PackError(
                    f"two {label} packs claim {list(identity)}: "
                    f"{seen[(label, identity)]!r} and {name!r}. One key, one reviewed pack: "
                    "give them different verticals or delete the duplicate."
                )
            seen[(label, identity)] = name

    @staticmethod
    def _check_trigger(
        pack: DisclosurePack, spec: DisclosureSpec, procedure: ProcedurePack | None
    ) -> None:
        trigger = spec.trigger_event
        if trigger == TRIGGER_CONTACT_START:
            return
        if trigger.startswith(_TRIGGER_STATE):
            state_id = trigger[len(_TRIGGER_STATE) :]
            if procedure is None or state_id not in procedure.state_ids:
                raise PackError(
                    f"disclosure pack {pack.pack_id}: {spec.disclosure_id!r} triggers on "
                    f"procedure state {state_id!r}, which the {pack.market}/{pack.vertical} "
                    "procedure pack does not define (a window that can never open is not a "
                    "disclosure requirement)"
                )
            return
        if trigger.startswith(_TRIGGER_LEXICON):
            entry_id = trigger[len(_TRIGGER_LEXICON) :]
            if procedure is None or entry_id not in procedure.lexicon.entry_ids:
                raise PackError(
                    f"disclosure pack {pack.pack_id}: {spec.disclosure_id!r} triggers on lexicon "
                    f"entry {entry_id!r}, which the {pack.market}/{pack.vertical} procedure pack "
                    "does not define"
                )
            return
        raise PackError(
            f"disclosure pack {pack.pack_id}: {spec.disclosure_id!r} has trigger_event "
            f"{trigger!r}. Use {TRIGGER_CONTACT_START!r}, "
            f"'{_TRIGGER_STATE}<state_id>' or '{_TRIGGER_LEXICON}<entry_id>'."
        )
