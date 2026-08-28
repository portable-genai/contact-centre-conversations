"""PartyRecordsPort: does THIS party own the record a parameter names?

The gate answers "may this contact ask for a card balance". It cannot answer "may this contact
ask for THAT card's balance", because until now nothing in the domain could say whose card it
was. A four-digit parameter validated against ``[0-9]{4}`` is indistinguishable from any other
four digits, so an allowed intent plus a well-formed parameter was enough to read a stranger's
balance. Tenant partition does not help: two customers of one bank are the same tenant.

So ownership is its own question, asked of its own port, once per parameter the catalog declares
as naming a party-owned record. The port answers only yes or no and never returns the record
itself: a lookup that returned data would tempt a caller into using it, and this call happens
BEFORE the maker-checker decision.

Fails closed in three ways, all of them deliberate:

* an unidentified party (``party_ref`` empty) owns nothing, so a contact that has not yet
  verified who it is speaking to cannot read anybody's records, including the right person's;
* an adapter that cannot answer RAISES rather than returning False, because "we could not check"
  and "we checked and it is not theirs" are different facts and only one of them is about the
  caller;
* a parameter the catalog forgot to classify is not silently unchecked: ``binds_to_party`` is a
  required field on every parameter, for the same reason ``consequential`` is required on every
  action.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PartyRecordsPort(Protocol):
    def owns(self, *, party_ref: str, tenant: str, parameter: str, value: str) -> bool:
        """True when ``party_ref`` owns the record ``value`` names under ``parameter``.

        Never raises to mean "no". An adapter that cannot reach its record system raises, and
        the caller turns that into a refusal plus an escalation rather than a quiet denial.
        """
        ...
