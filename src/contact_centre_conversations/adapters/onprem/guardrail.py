"""On-prem GuardrailPort: fail-fast portability placeholder (P-12).

The client screens with its own gateway. The refusal matters more here than anywhere else: this
port's failure mode is a fabricated CLEAN, and a placeholder that returned one would switch off
injection screening for a whole deployment while every offline test stayed green.
``TurnGuard`` converts this raise into UNAVAILABLE, which fails closed per mode.
"""

from __future__ import annotations

from ...config import Settings
from ...domain.models import ScreenResult


class OnPremGuardrailAdapter:
    """Satisfies GuardrailPort but refuses: bind the client's own screening gateway."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def screen(self, text: str, *, turn_index: int = 0) -> ScreenResult:
        raise NotImplementedError(
            "on-prem guardrail screening is a portability placeholder: bind the client's own "
            "gateway (see docs/onprem-migration.md). It must never return CLEAN without "
            "screening."
        )
