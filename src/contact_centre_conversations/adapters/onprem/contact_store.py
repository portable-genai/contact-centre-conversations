"""On-prem ContactStorePort: fail-fast portability placeholder (P-12).

The client's contact records stay in the client's database. Every method refuses, including the
readers: a reader that returned an empty sequence would be indistinguishable from a contact with
no turns, and the whole transcript would silently become empty.
"""

from __future__ import annotations

from collections.abc import Sequence

from speech_lexicon_kit import SpeakerTurn

from ...config import Settings
from ...domain.models import ContactRef

_MESSAGE = (
    "on-prem contact persistence is a portability placeholder: bind the client's own store "
    "(see docs/onprem-migration.md). Tenant authorisation is the client's to enforce too, and "
    "a cross-tenant read must answer 403 rather than 404."
)


class OnPremContactStore:
    """Satisfies ContactStorePort but refuses on every method."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def create(self, contact: ContactRef) -> None:
        raise NotImplementedError(_MESSAGE)

    def append_turn(self, contact_id: str, turn: SpeakerTurn, *, tenant: str) -> None:
        raise NotImplementedError(_MESSAGE)

    def turns(self, contact_id: str, *, tenant: str) -> Sequence[SpeakerTurn]:
        raise NotImplementedError(_MESSAGE)

    def contact(self, contact_id: str, *, tenant: str) -> ContactRef | None:
        raise NotImplementedError(_MESSAGE)
