"""Domain errors that carry the HTTP status the API must answer with.

A status is a policy decision, not a transport detail, so it is decided next to the rule that
raises rather than in a handler that guesses from the exception's name.
"""

from __future__ import annotations

__all__ = ["ContactNotFoundError", "TenantMismatchError"]


class TenantMismatchError(PermissionError):
    """A principal asked about a contact belonging to another tenant.

    **403, not 404.** See ``ports/contact_store.py`` for why: a contact id is not a secret in
    this vertical (it is the customer's own reference, it is in the channel's logs and it is in
    the agent's CRM), so hiding existence buys nothing and costs an operator the ability to tell
    "you may not read this" from "this has been lost". A vertical whose ids ARE secret needs the
    other answer and a written reason.
    """

    http_status: int = 403


class ContactNotFoundError(LookupError):
    """No contact with that id exists under this tenant."""

    http_status: int = 404
