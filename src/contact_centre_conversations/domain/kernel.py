"""Vertical-neutral domain kernel: pure-stdlib types the service reasons over.

Taxonomies are ``StrEnum``s from the commons (a member IS its wire value), citations carry
provenance, and the WORM audit record is stored already-redacted. Nothing here imports a web
framework or a cloud SDK (the commons packages it uses are themselves stdlib).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from hex_service_kit.enums import LenientStrEnum


def utcnow() -> datetime:
    """Timezone-aware UTC now (the single clock the domain uses)."""
    return datetime.now(UTC)


class Severity(LenientStrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Decision(LenientStrEnum):
    ALLOWED = "allowed"
    ESCALATED = "escalated"  # routed to a human (maker-checker, P-06)


@dataclass(frozen=True, slots=True)
class Citation:
    """Provenance attached to a generated claim: the source, and where a reader can find it.

    ``source_id`` is this deployment's internal handle for the passage. ``source_ref`` is the
    published document a person could actually look up, which is the half a customer needs: a
    citation that resolves only inside the bank is provenance for the bank, not for the person
    being told something. It is empty only for provenance that names a pack rather than a
    document, where the internal id IS the reference.
    """

    source_id: str
    title: str
    snippet: str = ""
    source_ref: str = ""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """An immutable, already-redacted record of one interaction (P-04 / rule R2)."""

    action: str
    actor: str
    decision: Decision
    severity: Severity
    redacted_summary: str
    citations: tuple[Citation, ...] = ()
    timestamp: datetime = field(default_factory=utcnow)
    #: Which separately gated mode produced this record, where the service has modes. Empty for
    #: a record that belongs to neither. It is a field rather than a prefix on ``action``
    #: because each mode promotes on its own evidence, so "every decision this mode made" has to
    #: be a query somebody can run without parsing strings.
    mode: str = ""
    #: The contact this record belongs to, so a whole contact's trail can be assembled. Empty
    #: for records that are not about one contact.
    contact_id: str = ""
    #: The tenant partition, carried on the record itself: an audit trail that cannot be
    #: partitioned cannot be shown to one client without showing another's.
    tenant: str = ""
