"""Managed ContactStorePort: Firestore, in one region, partitioned by tenant.

The SDK import is LAZY so the offline profiles import this module with no cloud SDK. The tenant
partition is part of the DOCUMENT PATH rather than a filter on a query: a query filter can be
forgotten by the next method somebody adds, and a path cannot.

Cross-tenant access raises ``TenantMismatchError``, which the API answers as 403 rather than
404, for the reason written in ``ports/contact_store.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from speech_lexicon_kit import ChannelRole, SpeakerTurn

from ...config import Settings
from ...domain.errors import TenantMismatchError
from ...domain.models import ContactChannel, ContactRef
from ...domain.modes import ContactMode

_ROOT = "contact_centre_conversations"


class FirestoreContactStore:
    """Persist contacts and already-redacted turns under a per-tenant document path."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None

    def _db(self) -> Any:
        if self._client is None:
            # Lazy: this module must import with no cloud SDK installed.
            from google.cloud import firestore  # noqa: PLC0415

            self._client = firestore.Client()
        return self._client

    def _contact_doc(self, tenant: str, contact_id: str) -> Any:
        if not tenant.strip():
            raise TenantMismatchError(
                "no tenant partition was supplied: an unpartitioned read cannot be authorised"
            )
        return (
            self._db()
            .collection(_ROOT)
            .document(tenant)
            .collection("contacts")
            .document(contact_id)
        )

    def create(self, contact: ContactRef) -> None:
        self._contact_doc(contact.tenant, contact.contact_id).set(
            {
                "contact_id": contact.contact_id,
                "tenant": contact.tenant,
                "market": contact.market,
                "locale": contact.locale,
                "vertical": contact.vertical,
                "mode": contact.mode.value,
                "channel": contact.channel.value,
            },
            merge=True,
        )

    def append_turn(self, contact_id: str, turn: SpeakerTurn, *, tenant: str) -> None:
        self._contact_doc(tenant, contact_id).collection("turns").document(str(turn.index)).set(
            {
                "index": turn.index,
                "speaker_id": turn.speaker_id,
                "role": turn.role.value,
                "text": turn.text,
                "start_ms": turn.start_ms,
                "end_ms": turn.end_ms,
            }
        )

    def turns(self, contact_id: str, *, tenant: str) -> Sequence[SpeakerTurn]:
        docs = self._contact_doc(tenant, contact_id).collection("turns").order_by("index").stream()
        return tuple(
            SpeakerTurn(
                index=int(row.get("index", 0)),
                speaker_id=str(row.get("speaker_id", "")),
                role=ChannelRole(str(row.get("role", "unknown"))),
                text=str(row.get("text", "")),
                start_ms=row.get("start_ms"),
                end_ms=row.get("end_ms"),
            )
            for row in (doc.to_dict() or {} for doc in docs)
        )

    def contact(self, contact_id: str, *, tenant: str) -> ContactRef | None:
        snapshot = self._contact_doc(tenant, contact_id).get()
        if not snapshot.exists:
            return None
        row = snapshot.to_dict() or {}
        if str(row.get("tenant", "")) != tenant:
            raise TenantMismatchError(f"contact {contact_id!r} belongs to another tenant")
        return ContactRef(
            contact_id=str(row.get("contact_id", contact_id)),
            tenant=tenant,
            market=str(row.get("market", "")),
            locale=str(row.get("locale", "")),
            vertical=str(row.get("vertical", "")),
            mode=ContactMode(str(row.get("mode", ContactMode.AGENT_ASSIST.value))),
            channel=ContactChannel(str(row.get("channel", ContactChannel.VOICE.value))),
        )
