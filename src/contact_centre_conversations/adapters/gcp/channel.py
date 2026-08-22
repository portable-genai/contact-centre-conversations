"""Managed ConversationChannelPort: Dialogflow CX / CCAI, behind the port.

The SDK import is LAZY, so the offline profiles import this module with nothing installed. The
port stays about TURNS: the channel delivers text whether it arrived as a chat message or as
recognised speech, and no engine downstream can tell the difference, which is what lets one
orchestrator serve voice and chat.

``turns`` is a streaming read on the managed side, so it is written as a generator here too: a
managed adapter whose shape differed from the offline one would make the offline replay a
different code path rather than the same one with different data.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from speech_lexicon_kit import ChannelRole

from ...config import Settings
from ...domain.models import ContactRef, TurnSubmission


class DialogflowChannel:
    """Open a CX session, stream inbound turns and deliver outbound messages."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _sessions(self) -> Any:
        from google.cloud import dialogflowcx_v3 as cx  # noqa: PLC0415

        return cx.SessionsClient()

    def _session_path(self, contact: ContactRef) -> str:
        return (
            f"projects/-/locations/{self._settings.region}/agents/-/sessions/{contact.contact_id}"
        )

    def open(self, contact: ContactRef) -> str:
        return self._session_path(contact)

    def turns(self, contact: ContactRef) -> Iterator[TurnSubmission]:
        client = self._sessions()
        stream = client.streaming_detect_intent(requests=iter(()))
        for index, response in enumerate(stream):
            query = getattr(response, "query_result", None)
            text = str(getattr(query, "transcript", "") or "")
            yield TurnSubmission(
                contact=contact,
                index=index,
                speaker_id="customer",
                role=ChannelRole.CUSTOMER,
                text=text,
            )

    def send(self, contact: ContactRef, text: str) -> str:
        client = self._sessions()
        response = client.detect_intent(
            request={
                "session": self._session_path(contact),
                "query_input": {"text": {"text": text}, "language_code": contact.locale},
            }
        )
        return str(getattr(response, "response_id", ""))
