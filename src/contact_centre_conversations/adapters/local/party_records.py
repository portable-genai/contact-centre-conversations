"""Local PartyRecordsPort: an offline ownership fixture that can actually say no.

Reads a JSON Lines fixture from ``parties_path`` (``config/parties/records.jsonl`` by default),
one row per record a party owns: ``party_ref``, ``tenant``, ``parameter``, ``value``. Ownership
is an exact match on all four, so the fixture is a positive list and anything not in it is not
owned. That is the whole point: a stand-in that answered True would make every ownership test
vacuous, and the metric built on it would be a tautology with a threshold.

Honest about its two failure modes, the same way the retrieval fixture is:

* a fixture file that was NAMED and does not exist raises, rather than answering "not owned"
  for everybody and turning a broken deployment into a service that refuses every customer;
* an EMPTY fixture raises, because a records system that knows about nobody is a misconfiguration
  and not a bank whose customers own nothing.

It is a stand-in for the client's system of record, not a system of record.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...config import Settings


class LocalFixturePartyRecords:
    """Answer ownership from a fictional fixture, by exact match on all four fields."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._path = Path(settings.parties_path) if settings.parties_path else None

    def _rows(self) -> list[dict[str, str]]:
        if self._path is None:
            raise RuntimeError(
                "no party-records fixture is configured (parties_path is empty), so ownership "
                "cannot be checked. Point parties_path at a records file or bind the client's "
                "own system of record."
            )
        if not self._path.exists():
            raise RuntimeError(f"party-records fixture {self._path} does not exist")
        rows: list[dict[str, str]] = []
        for raw in self._path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            rows.append({str(key): str(value) for key, value in row.items()})
        if not rows:
            raise RuntimeError(
                f"party-records fixture {self._path} is empty; a records system that knows about "
                "nobody is a misconfiguration, not a bank whose customers own nothing"
            )
        return rows

    def owns(self, *, party_ref: str, tenant: str, parameter: str, value: str) -> bool:
        # An unidentified party owns nothing. Checked before the fixture is even read, so a
        # contact that has not verified who it is speaking to fails closed even where the
        # records system is unreachable.
        if not party_ref.strip():
            return False
        for row in self._rows():
            if (
                row.get("party_ref") == party_ref
                and row.get("tenant") == tenant
                and row.get("parameter") == parameter
                and row.get("value") == value
            ):
                return True
        return False
