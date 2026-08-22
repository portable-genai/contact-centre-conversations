"""ConversationChannelPort: the CCAI / Dialogflow channel, behind its own boundary.

The managed channel is a whole product with its own session model, its own webhooks and its own
SDK. Putting it behind a port with a deterministic offline adapter is what keeps the hard gate
SDK-free while the same code paths run in front of the real thing: the local adapter replays a
scripted contact turn by turn, so a test and a demo drive the identical orchestrator the managed
channel drives.

The port is deliberately about TURNS, not audio. Speech lives in ``ports/speech.py``; a channel
delivers turns whether they arrived as text or as recognised speech, and no engine downstream
should be able to tell the difference.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from ..domain.models import ContactRef, TurnSubmission


@runtime_checkable
class ConversationChannelPort(Protocol):
    def open(self, contact: ContactRef) -> str:
        """Open a channel session for ``contact`` and return the channel's own session id."""
        ...

    def turns(self, contact: ContactRef) -> Iterator[TurnSubmission]:
        """Yield inbound turns in order. Raw text: the guard redacts and screens them."""
        ...

    def send(self, contact: ContactRef, text: str) -> str:
        """Deliver one outbound message and return the channel's message reference."""
        ...
