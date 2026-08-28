"""Managed PartyRecordsPort: ask the client's system of record who owns what.

One question over the same S2S transport the guardrail screen, the governed-RAG retrieval and
the MCP action catalog use: does this party own this record. The answer is a boolean and the
record itself never crosses the wire, because this call happens BEFORE the maker-checker
decision and a response carrying data would be data fetched for a request that may yet be
refused.

An answer that is not an explicit boolean is not an answer. A records service that replied with
an empty body, an error shape or a field of another type would otherwise be read as False, which
reads to a customer exactly like being told their own card is not theirs, and reads to an
operator like a working deployment. So anything unparseable raises.
"""

from __future__ import annotations

from ...config import Settings
from ._s2s import post_json, require_base_url


class PlatformPartyRecords:
    """Resolve record ownership through the client's system of record."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return require_base_url(self._settings.party_records_url, what="party_records_url")

    def owns(self, *, party_ref: str, tenant: str, parameter: str, value: str) -> bool:
        # An unidentified party owns nothing, and asking is pointless: fail closed without a
        # round trip, so an unverified contact cannot be answered even by a permissive service.
        if not party_ref.strip():
            return False
        payload = post_json(
            self._base(),
            "/v1/parties/owns",
            {"party_ref": party_ref, "tenant": tenant, "parameter": parameter, "value": value},
            actor=tenant,
        )
        owned = payload.get("owns")
        if not isinstance(owned, bool):
            raise RuntimeError(
                "the party-records service did not answer with a boolean 'owns' field; refusing "
                "rather than reading an unparseable answer as 'not theirs'"
            )
        return owned
