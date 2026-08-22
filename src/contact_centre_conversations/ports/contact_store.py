"""ContactStorePort: tenant-scoped persistence of contacts, turns and their artifacts.

The store is where cross-tenant authorisation becomes real rather than aspirational. The domain
authorises against the VERIFIED principal's tenant and the store raises
:class:`~..domain.errors.TenantMismatchError`, which the API answers as **403, not 404**.

404 would be the cautious-looking choice and it is the wrong one here. A contact id is not a
secret: it appears in the customer's own reference number, in the channel's logs and in the
agent's CRM. Answering 404 to a real id would tell an authorised operator that their data had
been lost, which produces an incident; answering 403 tells the truth, which is that the id
exists and this principal may not read it. Where an id IS a secret, 404 is right, and this port
would need a different rule and a written reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from speech_lexicon_kit import SpeakerTurn

from ..domain.models import ContactRef


@runtime_checkable
class ContactStorePort(Protocol):
    def create(self, contact: ContactRef) -> None:
        """Register a contact under its tenant. Re-registering the same id is idempotent."""
        ...

    def append_turn(self, contact_id: str, turn: SpeakerTurn, *, tenant: str) -> None:
        """Append one ALREADY-REDACTED turn, refusing when ``tenant`` does not own the contact."""
        ...

    def turns(self, contact_id: str, *, tenant: str) -> Sequence[SpeakerTurn]:
        """Every stored turn, refusing when ``tenant`` does not own the contact."""
        ...

    def contact(self, contact_id: str, *, tenant: str) -> ContactRef | None:
        """The contact, or None when no such id exists; refuses on a tenant mismatch."""
        ...
