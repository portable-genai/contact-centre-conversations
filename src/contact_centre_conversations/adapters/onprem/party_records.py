"""On-prem PartyRecordsPort: fail-fast portability placeholder (the sovereign-exit proof, P-12).

The client owns the system of record that knows which customer holds which card, policy or
claim. This binding refuses at call time rather than answering. Refusing is the correct failure:
an adapter that returned False would refuse every legitimate customer while looking like a
working deployment, and one that returned True would hand every customer everybody else's data.
"""

from __future__ import annotations

from ...config import Settings


class OnPremPartyRecords:
    """Satisfies PartyRecordsPort but refuses: the client wires its own system of record."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def owns(self, *, party_ref: str, tenant: str, parameter: str, value: str) -> bool:
        raise NotImplementedError(
            "on-prem party-record ownership is a portability placeholder: bind the client's own "
            "system of record (see docs/onprem-migration.md). Until it is bound, no action whose "
            "parameters name a party-owned record may execute."
        )
