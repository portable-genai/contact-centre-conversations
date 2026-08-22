"""Local ConversationChannelPort: replay a scripted contact, text and simulated voice alike.

The managed channel is Dialogflow CX / CCAI. This adapter drives the identical orchestrator from
a scripted stream file, so the hard gate exercises the whole turn pipeline with no SDK, no
project and no audio, and the demo runs from a checkout on a locked-down laptop.

Outbound messages are collected rather than sent, which is what lets a test assert what a
customer WOULD have been told without a channel in the room.
"""

from __future__ import annotations

from collections.abc import Iterator

from speech_lexicon_kit import ChannelRole

from ...config import Settings
from ...domain.models import ContactRef, TurnSubmission
from .speech import FIXTURE_SCHEME, LocalReplaySpeechAdapter


class LocalScriptedChannel:
    """Yield scripted inbound turns; collect outbound messages instead of sending them."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._speech = LocalReplaySpeechAdapter(settings)
        self._sent: list[tuple[str, str]] = []

    @property
    def sent(self) -> tuple[tuple[str, str], ...]:
        """``(contact_id, text)`` for every outbound message, in order."""
        return tuple(self._sent)

    def open(self, contact: ContactRef) -> str:
        return f"local-session:{contact.contact_id}"

    def turns(self, contact: ContactRef) -> Iterator[TurnSubmission]:
        rows = self._speech.script(f"{FIXTURE_SCHEME}{contact.contact_id}")
        total = len(rows)
        for index, row in enumerate(rows):
            yield TurnSubmission(
                contact=contact,
                index=index,
                speaker_id=str(row.get("speaker_id", f"s{index}")),
                role=ChannelRole(str(row.get("role", "unknown"))),
                text=str(row.get("text", "")),
                start_ms=_as_int(row.get("start_ms")),
                end_ms=_as_int(row.get("end_ms")),
                ends_contact=index == total - 1,
            )

    def send(self, contact: ContactRef, text: str) -> str:
        self._sent.append((contact.contact_id, text))
        return f"local-message:{len(self._sent)}"


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(str(value))
