"""A customer-facing reply may quote only what the bank has published.

A contact centre's knowledge base holds both halves of every topic: the publishable rule, and
how staff are meant to handle it. They read alike. Before passages were classified, both modes
ran the same retrieval over the same undifferentiated pool, so the only thing standing between a
customer and an internal handling note was which passage happened to rank higher. The shipped
corpus proved the point: the reason pending authorisations are withheld sat in the same row as
the customer-facing listing rule, in the only pool a self-service reply could be grounded in.

The control is the retrieval FILTER, and the check in ``validate_draft`` is the proof. Both are
here, because they fail differently: the filter is what a governed knowledge base enforces, and
the validator is what catches a retrieval implementation that ignored it or a passage
reclassified after it was indexed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from contact_centre_conversations.config import build_container
from contact_centre_conversations.domain import suggestions
from contact_centre_conversations.domain.kernel import Citation
from contact_centre_conversations.domain.models import (
    AUDIENCE_INTERNAL,
    AUDIENCE_PUBLIC,
    RetrievedPassage,
)
from contact_centre_conversations.domain.modes import ContactMode

from tests.conftest import local_settings

_INTERNAL_PASSAGE = "kb-sg-006"


def _passage(audience: str, passage_id: str = "kb-sg-001") -> RetrievedPassage:
    return RetrievedPassage(
        text="The balance quoted is the posted balance.",
        citation=Citation(source_id=passage_id, title="Card balance", source_ref="ref"),
        audience=audience,
    )


def _draft(passage_id: str = "kb-sg-001") -> dict[str, object]:
    return {"text": "The balance quoted is the posted balance.", "passage_ids": [passage_id]}


# ------------------------------------------------------------------ the retrieval filter
def test_a_customer_facing_query_asks_only_for_public_passages() -> None:
    query = suggestions.build_query(
        "balance", market="SG", locale="en-SG", mode=ContactMode.SELF_SERVICE
    )
    assert query.filters["audience"] == AUDIENCE_PUBLIC


def test_an_agent_facing_query_sets_no_audience_filter_at_all() -> None:
    """Agent-assist may see both, and a filter naming every value excludes nothing.

    Setting ``audience`` to a list of everything permitted would look like a control while
    being one, which is worse than not setting it: a reader would stop asking.
    """
    query = suggestions.build_query(
        "balance", market="SG", locale="en-SG", mode=ContactMode.AGENT_ASSIST
    )
    assert "audience" not in query.filters


def test_the_corpus_never_returns_an_internal_passage_to_a_customer() -> None:
    adapter = build_container(local_settings()).retrieval
    query = suggestions.build_query(
        "supervisor referral identity checks fail",
        market="SG",
        locale="en-SG",
        mode=ContactMode.SELF_SERVICE,
    )
    assert all(passage.audience == AUDIENCE_PUBLIC for passage in adapter.retrieve(query))


def test_the_same_query_does_reach_that_passage_for_an_agent() -> None:
    """Or the test above is satisfied by a corpus that simply never returns anything."""
    adapter = build_container(local_settings()).retrieval
    query = suggestions.build_query(
        "supervisor referral identity checks fail",
        market="SG",
        locale="en-SG",
        mode=ContactMode.AGENT_ASSIST,
    )
    returned = {passage.citation.source_id for passage in adapter.retrieve(query)}
    assert _INTERNAL_PASSAGE in returned


# ------------------------------------------------------------------ the validator
def test_a_customer_draft_citing_an_internal_passage_is_discarded_whole() -> None:
    """The defence-in-depth half: a retrieval that ignored the filter still reaches nobody."""
    passages = (_passage(AUDIENCE_INTERNAL),)
    assert suggestions.validate_draft(_draft(), passages, mode=ContactMode.SELF_SERVICE) is None


def test_the_same_draft_over_a_public_passage_is_accepted() -> None:
    passages = (_passage(AUDIENCE_PUBLIC),)
    reply = suggestions.validate_draft(_draft(), passages, mode=ContactMode.SELF_SERVICE)
    assert reply is not None and reply.passage_ids == ("kb-sg-001",)


def test_an_agent_draft_over_an_internal_passage_is_accepted() -> None:
    """The asymmetry is the point: a trained employee is MEANT to see handling rules."""
    passages = (_passage(AUDIENCE_INTERNAL),)
    reply = suggestions.validate_draft(_draft(), passages, mode=ContactMode.AGENT_ASSIST)
    assert reply is not None


def test_one_internal_passage_among_several_discards_the_whole_draft() -> None:
    """No partial acceptance: dropping the offending citation would leave the text asserting it."""
    passages = (
        _passage(AUDIENCE_PUBLIC, "kb-sg-001"),
        _passage(AUDIENCE_INTERNAL, "kb-sg-006"),
    )
    payload = {
        "text": "The balance quoted is the posted balance.",
        "passage_ids": ["kb-sg-001", "kb-sg-006"],
    }
    assert suggestions.validate_draft(payload, passages, mode=ContactMode.SELF_SERVICE) is None


# ------------------------------------------------------------------ the corpus contract
def test_every_shipped_passage_is_classified_and_resolvable() -> None:
    adapter = build_container(local_settings()).retrieval
    query = suggestions.build_query(
        "card balance transactions", market="SG", locale="en-SG", mode=ContactMode.AGENT_ASSIST
    )
    passages = adapter.retrieve(query)
    assert passages
    for passage in passages:
        assert passage.audience in (AUDIENCE_PUBLIC, AUDIENCE_INTERNAL)
        assert passage.citation.source_ref


def test_an_unclassified_passage_is_refused_at_load(tmp_path) -> None:
    """Refused at LOAD, because the filter cannot exclude on a field the row does not carry.

    An unclassified row would match a public-only query on exactly the branch that treats an
    absent key as no constraint, so it would be quoted to a customer.
    """
    corpus = tmp_path / "passages.jsonl"
    corpus.write_text(
        '{"passage_id": "kb-x-001", "title": "T", "text": "A rule.", "market": "SG", '
        '"locale": "en-SG", "source_ref": "ref"}\n',
        encoding="utf-8",
    )
    adapter = build_container(local_settings(kb_path=str(corpus))).retrieval
    with pytest.raises(RuntimeError, match="declares audience"):
        adapter.retrieve(
            suggestions.build_query(
                "rule", market="SG", locale="en-SG", mode=ContactMode.SELF_SERVICE
            )
        )


def test_a_passage_with_no_source_ref_is_refused_at_load(tmp_path) -> None:
    corpus = tmp_path / "passages.jsonl"
    corpus.write_text(
        '{"passage_id": "kb-x-001", "title": "T", "text": "A rule.", "market": "SG", '
        '"locale": "en-SG", "audience": "public"}\n',
        encoding="utf-8",
    )
    adapter = build_container(local_settings(kb_path=str(corpus))).retrieval
    with pytest.raises(RuntimeError, match="names no source_ref"):
        adapter.retrieve(
            suggestions.build_query(
                "rule", market="SG", locale="en-SG", mode=ContactMode.SELF_SERVICE
            )
        )


def test_a_customer_receives_a_reference_they_could_look_up() -> None:
    """The other half of provenance: an internal id is provenance for the bank, not the person."""
    passages = (_passage(AUDIENCE_PUBLIC),)
    reply = suggestions.validate_draft(_draft(), passages, mode=ContactMode.SELF_SERVICE)
    assert reply is not None
    assert all(citation.source_ref for citation in reply.citations)


def test_the_permitted_audiences_are_declared_per_mode_not_derived() -> None:
    """A third mode has to state its position rather than inherit the laxer one."""
    assert suggestions.AUDIENCES_FOR_MODE[ContactMode.SELF_SERVICE] == frozenset({AUDIENCE_PUBLIC})
    assert AUDIENCE_INTERNAL in suggestions.AUDIENCES_FOR_MODE[ContactMode.AGENT_ASSIST]


def test_the_customer_facing_pool_is_a_strict_subset_of_the_agent_pool() -> None:
    """Stated as a property, so a future reclassification cannot widen one without the other."""
    customer = suggestions.AUDIENCES_FOR_MODE[ContactMode.SELF_SERVICE]
    agent = suggestions.AUDIENCES_FOR_MODE[ContactMode.AGENT_ASSIST]
    assert customer < agent


def test_a_reclassified_passage_stops_reaching_customers(tmp_path) -> None:
    """The corpus is data: flipping a row to internal must change what a customer can be told."""
    row = (
        '{{"passage_id": "kb-x-001", "title": "Fees", "text": "A monthly fee applies.", '
        '"market": "SG", "locale": "en-SG", "audience": "{audience}", "source_ref": "ref"}}\n'
    )
    query = suggestions.build_query(
        "monthly fee applies", market="SG", locale="en-SG", mode=ContactMode.SELF_SERVICE
    )

    public = tmp_path / "public.jsonl"
    public.write_text(row.format(audience=AUDIENCE_PUBLIC), encoding="utf-8")
    assert build_container(local_settings(kb_path=str(public))).retrieval.retrieve(query)

    internal = tmp_path / "internal.jsonl"
    internal.write_text(row.format(audience=AUDIENCE_INTERNAL), encoding="utf-8")
    assert build_container(local_settings(kb_path=str(internal))).retrieval.retrieve(query) == []


def test_the_replaced_passage_keeps_its_own_audience_when_copied() -> None:
    """``replace`` on a passage must not quietly reset the classification to the default."""
    original = _passage(AUDIENCE_INTERNAL)
    assert replace(original, score=0.9).audience == AUDIENCE_INTERNAL
