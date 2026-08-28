"""Packs are selected by ``(market, vertical)``, and a contested key is a LOAD failure.

Before the key carried a vertical, ``procedure_for("SG")`` was ``next(p for p in procedures if
p.market == market)``. A bank and an insurer both operate in SG, so a second SG procedure pack
loaded, validated, and then lost silently to whichever filename sorted first: the service ran on
a compliance artifact chosen by alphabetical accident and nothing anywhere said so. That is the
defect this module holds shut, and it is held shut from both directions:

* two packs claiming one key must REFUSE, so the ambiguity can never be resolved by luck;
* two packs differing only in vertical must both load AND be separately selectable, so the
  refusal above is a real key and not merely a ban on similar-looking packs.

The second half is the one that would rot quietly. A guard that refused every second SG pack
would pass the first test forever while making a second line of business impossible, which is
the opposite of the point.
"""

from __future__ import annotations

from typing import Any

import pytest

from contact_centre_conversations.domain.packs import PackError, PackLibrary

_BANKING = "retail_banking"
_INSURANCE = "general_insurance"


def _procedure(*, pack_id: str, market: str, vertical: str) -> dict[str, Any]:
    """The smallest procedure pack the parser accepts, so the test is about the KEY."""
    return {
        "kind": "procedure",
        "pack_id": pack_id,
        "market": market,
        "vertical": vertical,
        "locale": "en-SG",
        "initial_state": "greeting",
        "lexicon": [{"entry_id": "greeted", "phrases": ["thank you for calling"]}],
        "states": [
            {
                "state_id": "greeting",
                "title": "Greet the caller",
                "instruction": "Greet the caller and say why the call is recorded.",
                "exit_criteria": ["greeted"],
                "required_evidence": ["greeted"],
                "transitions": [],
            }
        ],
    }


def _catalog(*, catalog_id: str, vertical: str, action_id: str) -> dict[str, Any]:
    return {
        "kind": "actions",
        "catalog_id": catalog_id,
        "vertical": vertical,
        "actions": [
            {
                "action_id": action_id,
                "title": f"Do {action_id}",
                "consequential": False,
                "severity": "low",
                "parameters": [],
            }
        ],
    }


def test_two_procedure_packs_claiming_one_market_and_vertical_are_refused_at_load() -> None:
    """The defect itself: an ambiguous key must never be resolved by sort order."""
    with pytest.raises(PackError, match="two procedure packs claim"):
        PackLibrary.from_documents(
            [
                _procedure(pack_id="sg-card-dispute-v1", market="SG", vertical=_BANKING),
                _procedure(pack_id="sg-motor-claim-v1", market="SG", vertical=_BANKING),
            ]
        )


def test_the_refusal_names_both_packs_so_the_boot_error_is_actionable() -> None:
    """A refusal that named neither pack would send a reader to grep the whole packs directory."""
    with pytest.raises(PackError) as raised:
        PackLibrary.from_documents(
            [
                _procedure(pack_id="sg-card-dispute-v1", market="SG", vertical=_BANKING),
                _procedure(pack_id="sg-motor-claim-v1", market="SG", vertical=_BANKING),
            ]
        )
    message = str(raised.value)
    assert "sg-card-dispute-v1" in message
    assert "sg-motor-claim-v1" in message


def test_the_same_market_under_two_verticals_loads_and_each_is_selectable() -> None:
    """The other direction: the key DISCRIMINATES, it does not merely ban a second SG pack."""
    library = PackLibrary.from_documents(
        [
            _procedure(pack_id="sg-card-dispute-v1", market="SG", vertical=_BANKING),
            _procedure(pack_id="sg-motor-claim-v1", market="SG", vertical=_INSURANCE),
        ]
    )
    banking = library.procedure_for("SG", _BANKING)
    insurance = library.procedure_for("SG", _INSURANCE)
    assert banking is not None and banking.pack_id == "sg-card-dispute-v1"
    assert insurance is not None and insurance.pack_id == "sg-motor-claim-v1"


def test_a_vertical_nobody_configured_is_absent_rather_than_answered_by_another() -> None:
    """Absent is the honest answer. Falling back to another line of business is the defect."""
    library = PackLibrary.from_documents(
        [_procedure(pack_id="sg-card-dispute-v1", market="SG", vertical=_BANKING)]
    )
    assert library.procedure_for("SG", _INSURANCE) is None


def test_an_action_catalog_answers_only_for_its_own_vertical() -> None:
    """Two lines of business may declare one action_id and mean different things by it."""
    library = PackLibrary.from_documents(
        [
            _catalog(catalog_id="retail-actions-v1", vertical=_BANKING, action_id="cancel_policy"),
        ]
    )
    assert library.action_spec("cancel_policy", _BANKING) is not None
    assert library.action_spec("cancel_policy", _INSURANCE) is None


def test_an_allowlist_may_not_name_an_action_from_another_verticals_catalog() -> None:
    """Cross-reference checking is per vertical, or the scoping above would be decorative."""
    with pytest.raises(PackError, match="not declared by any general_insurance action catalog"):
        PackLibrary.from_documents(
            [
                _catalog(
                    catalog_id="retail-actions-v1", vertical=_BANKING, action_id="read_balance"
                ),
                {
                    "kind": "allowlist",
                    "tenant": "demo-insurer",
                    "market": "SG",
                    "vertical": _INSURANCE,
                    "locale": "en-SG",
                    "intents": [],
                    "actions": ["read_balance"],
                },
            ]
        )
