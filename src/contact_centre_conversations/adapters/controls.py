"""The runtime-control seam: what a switched-off control binds, and what a caller reports.

**Disabled adapters.** When a deployment switches a cheap runtime control off
(``CONTACT_GUARDRAIL``, ``CONTACT_REVIEW_ROUTING``), the container binds one of these instead
of the profile's class. Each satisfies its port and does nothing, so no service grows a
``None`` branch, and the container logs the posture at startup.

The disabled guardrail returns ``CLEAN`` for a turn it did not screen, which the port otherwise
forbids. That is the one place it may: the deployment stated the posture, the startup warning
names it, and the verdict's ``detail`` says ``guardrail off`` rather than passing for a screen.

**Recording wrapper.** Every caller that hands a result to the router (the two API routes, the
two agent tools, the CLI and the voice gateway) wraps the bound router in
:class:`RecordingReviewRouter`, so what it returns can say what happened: ``routed``,
``failed``, ``off`` or ``not_required``. A failure is logged and absorbed here rather than
failing a turn that is already decided and audited, but it is never invisible: the caller
reports ``failed`` and an empty reference, which nobody can mistake for a queued review.

``pii-kit`` masking in ``domain/pii.py`` protects the audit trail, tool results and the review
payload. It is not the request-path redaction control the contract switches, so there is no
redaction adapter here.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from ..config import Settings
from ..domain.models import ReviewableResult, ScreenOutcome, ScreenResult

_log = logging.getLogger(__name__)


class ReviewRouting(StrEnum):
    """What happened to the human-review hand-off for one result."""

    ROUTED = "routed"
    FAILED = "failed"
    OFF = "off"
    NOT_REQUIRED = "not_required"


# --------------------------------------------------------------------------- #
# Disabled adapters
# --------------------------------------------------------------------------- #
class DisabledGuardrail:
    """GuardrailPort with the guardrail switched off: every turn passes, text unchanged."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        return ScreenResult(
            outcome=ScreenOutcome.CLEAN, turn_index=turn_index, detail="guardrail off"
        )


class DisabledReviewRouter:
    """ReviewRouterPort with routing switched off: nothing is submitted anywhere."""

    enabled = False

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def route(self, result: ReviewableResult, *, maker: str, tenant: str = "") -> str:
        return ""


# --------------------------------------------------------------------------- #
# Per-call disclosure
# --------------------------------------------------------------------------- #
class RecordingReviewRouter:
    """Wraps the bound review router for one caller and records each hand-off's outcome.

    The outcomes are a set, not a log: only which outcomes occurred decides what the caller
    reports, and a long-lived wrapper (the voice gateway's) must not grow with every call.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self._outcomes: set[ReviewRouting] = set()

    def route(self, result: ReviewableResult, *, maker: str, tenant: str = "") -> str:
        """Hand ``result`` off if it requires review; return the reference, empty if none."""
        if not result.requires_human_review:
            return ""
        if not getattr(self._inner, "enabled", True):
            self._outcomes.add(ReviewRouting.OFF)
            return ""
        try:
            reference = self._inner.route(result, maker=maker, tenant=tenant)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the outcome is reported, never raised
            _log.warning("human-review hand-off failed: %s", type(exc).__name__)
            self._outcomes.add(ReviewRouting.FAILED)
            return ""
        self._outcomes.add(ReviewRouting.ROUTED)
        return str(reference)

    @property
    def outcome(self) -> ReviewRouting:
        """One value for the caller: any failure wins, then off, then routed."""
        for worst in (ReviewRouting.FAILED, ReviewRouting.OFF, ReviewRouting.ROUTED):
            if worst in self._outcomes:
                return worst
        return ReviewRouting.NOT_REQUIRED
