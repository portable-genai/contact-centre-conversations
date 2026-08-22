"""On-prem ConversationChannelPort: fail-fast portability placeholder (P-12).

The client's telephony and chat channels are theirs. ``turns`` refuses rather than yielding
nothing: an empty iterator is what a quiet contact looks like, and the orchestrator would then
run to completion having processed no turns at all.
"""

from __future__ import annotations

from collections.abc import Iterator

from ...config import Settings
from ...domain.models import ContactRef, TurnSubmission

_MESSAGE = (
    "on-prem conversation channels are a portability placeholder: bind the client's own "
    "telephony and chat channels (see docs/onprem-migration.md)."
)


class OnPremConversationChannel:
    """Satisfies ConversationChannelPort but refuses on every method."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def open(self, contact: ContactRef) -> str:
        raise NotImplementedError(_MESSAGE)

    def turns(self, contact: ContactRef) -> Iterator[TurnSubmission]:
        raise NotImplementedError(_MESSAGE)

    def send(self, contact: ContactRef, text: str) -> str:
        raise NotImplementedError(_MESSAGE)
