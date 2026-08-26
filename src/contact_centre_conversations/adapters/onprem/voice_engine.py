"""On-prem VoiceEnginePort: fail-fast portability placeholder (P-12).

The client's realtime speech stack is theirs: an on-prem ASR/TTS pair, or a self-hosted realtime
model, bound here behind the same port. ``connect`` refuses rather than returning a session that
would sit silent on a live call: a placeholder that answers a phone and says nothing is an
outage that looks like a bot.
"""

from __future__ import annotations

from ...config import Settings
from ...ports.voice_engine import (
    TRANSCRIBES_ONLY,
    VoiceEngineSession,
    VoiceSessionConfig,
)

_MESSAGE = (
    "on-prem voice engines are a portability placeholder: bind the client's own realtime "
    "speech stack (see docs/onprem-migration.md)."
)


class OnPremVoiceEngine:
    """Satisfies VoiceEnginePort but refuses to open any session."""

    speech_authorship = TRANSCRIBES_ONLY

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def connect(self, config: VoiceSessionConfig) -> VoiceEngineSession:
        raise NotImplementedError(_MESSAGE)
