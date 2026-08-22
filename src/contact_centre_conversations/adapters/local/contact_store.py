"""Local ContactStorePort: an in-process, tenant-partitioned contact and turn store.

Small, but it carries the whole authorisation rule, and that rule is asserted here rather than
in the API: a store that returned another tenant's turns would be a data leak whichever surface
called it, so the check lives at the boundary that owns the data.

Cross-tenant reads and writes raise
:class:`~...domain.errors.TenantMismatchError`, which the API answers as **403, not 404**. See
``ports/contact_store.py`` for why that is the right answer in this vertical.
"""

from __future__ import annotations

from collections.abc import Sequence

from speech_lexicon_kit import SpeakerTurn

from ...config import Settings
from ...domain.errors import TenantMismatchError
from ...domain.models import ContactRef

#: The store's data lives at MODULE scope, not on the instance, and that is deliberate.
#:
#: A contact store is a shared database. Two service instances in one deployment see the same
#: contact; so must two containers in one process, or an agent tool that builds a container per
#: call would lose the whole conversation between turns and the offline profile would model
#: something no real deployment does. :meth:`LocalContactStore.reset` clears it, and the test
#: suite calls that between tests rather than relying on construction to isolate them.
_CONTACTS: dict[tuple[str, str], ContactRef] = {}
_TURNS: dict[tuple[str, str], list[SpeakerTurn]] = {}


class LocalContactStore:
    """Hold contacts and their already-redacted turns for the offline profile."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @staticmethod
    def reset() -> None:
        """Empty the process-wide offline store (test and demo isolation)."""
        _CONTACTS.clear()
        _TURNS.clear()

    def create(self, contact: ContactRef) -> None:
        for owner, existing_id in list(_CONTACTS):
            if existing_id == contact.contact_id and owner != contact.tenant:
                raise TenantMismatchError(
                    f"contact {contact.contact_id!r} belongs to tenant {owner!r}"
                )
        key = (contact.tenant, contact.contact_id)
        _CONTACTS.setdefault(key, contact)
        _TURNS.setdefault(key, [])

    @staticmethod
    def _authorise(contact_id: str, tenant: str) -> ContactRef | None:
        """Resolve within THIS tenant, and refuse loudly when another tenant owns the id.

        Two questions, deliberately answered differently: an id nobody has is None (the caller
        turns that into "no such contact"), and an id ANOTHER tenant has RAISES, which the API
        answers as 403. Collapsing them into one answer is the "404 hides everything" choice,
        which costs an authorised operator the difference between "not yours" and "lost". See
        ``ports/contact_store.py`` for why this vertical wants the honest answer.
        """
        owned = _CONTACTS.get((tenant, contact_id))
        if owned is not None:
            return owned
        for owner, existing_id in _CONTACTS:
            if existing_id == contact_id:
                raise TenantMismatchError(
                    f"contact {contact_id!r} belongs to tenant {owner!r}, not {tenant!r}"
                )
        return None

    def append_turn(self, contact_id: str, turn: SpeakerTurn, *, tenant: str) -> None:
        if self._authorise(contact_id, tenant) is None:
            raise TenantMismatchError(f"no contact {contact_id!r} under tenant {tenant!r}")
        _TURNS.setdefault((tenant, contact_id), []).append(turn)

    def turns(self, contact_id: str, *, tenant: str) -> Sequence[SpeakerTurn]:
        if self._authorise(contact_id, tenant) is None:
            return ()
        return tuple(_TURNS.get((tenant, contact_id), ()))

    def contact(self, contact_id: str, *, tenant: str) -> ContactRef | None:
        return self._authorise(contact_id, tenant)
