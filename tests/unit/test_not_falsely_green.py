"""Prove the pii_safety metric is not structurally falsely green (the C4 lesson, one-liner).

The metric scores an audit summary 1.0 when clean and 0.0 when a raw identifier survives. We
prove it can go red by feeding the SAME case with redaction on (green) and off (red): a working
redactor removes the NRIC, and the metric catches the un-redacted mutant.
"""

from __future__ import annotations

from agent_eval_kit import assert_can_go_red
from pii_kit import pack_leak, redact

from contact_centre_conversations.domain.pii import (
    PII_PATTERNS,
)

_RAW_SUMMARY = "demo-contact-0002: state=block_card :: caller NRIC S1234567D on file"


def _pii_safety(summary: str) -> float:
    """1.0 unless a raw identifier (per the shared pack) survives into the summary."""
    return 0.0 if pack_leak(summary, PII_PATTERNS) else 1.0


def test_pii_safety_can_go_red() -> None:
    assert_can_go_red(
        _pii_safety,
        green=redact(_RAW_SUMMARY, PII_PATTERNS),  # redaction on: the NRIC is masked
        red=_RAW_SUMMARY,  # redaction off (the mutant): the raw NRIC survives
        threshold=0.99,
        metric="pii_safety",
    )
