"""The hexagon's boundaries, re-exported once so there is a single import site.

Every port is a ``@runtime_checkable`` Protocol and every port has a binding in every profile
(``config.DEFAULT_BINDINGS``); ``tests/test_contract_parity.py`` asserts both, plus set equality
in the reverse direction so a port added here without a binding fails the build.

``IdentityPort`` is not redeclared: it comes from the shared ``hex-service-kit`` commons and is
re-exported here so consumers still have one import site for the boundary set. What an identity
adapter DECLARES about the authentication it provides is this service's own vocabulary, not the
commons', and lives in :mod:`.identity` next to the re-export.

The three SPEECH ports come from ``speech-lexicon-kit`` for the same reason (see
:mod:`.speech`): a transcript, a speaker turn and a word offset must mean the same thing in
every repo that touches a contact centre, so the Protocols and their types have one owner.
"""

from __future__ import annotations

from hex_service_kit.identity import IdentityPort

from .audit import AuditSinkPort
from .contact_store import ContactStorePort
from .conversation_channel import ConversationChannelPort
from .generation import GenerationPort
from .guardrail import GuardrailPort
from .identity import (
    CLIENT_ASSERTED,
    END_USER_AUTH_ATTR,
    END_USER_AUTH_KINDS,
    UNIMPLEMENTED,
    VERIFIED,
    EndUserAuthUnavailableError,
    declared_end_user_auth,
)
from .observability import (
    EvaluationGatePort,
    ObservabilityTracerPort,
    TokenUsage,
)
from .retrieval import RetrievalPort
from .review_router import ReviewRouterPort
from .speech import DiarizationPort, SpeechToTextPort, TextToSpeechPort
from .tool_catalog import ToolCatalogPort

#: port name (the key in the settings ``adapters:`` block) -> the Protocol it must satisfy.
PORT_PROTOCOLS: dict[str, type] = {
    "audit": AuditSinkPort,
    "contact_store": ContactStorePort,
    "conversation_channel": ConversationChannelPort,
    "diarization": DiarizationPort,
    "generation": GenerationPort,
    "guardrail": GuardrailPort,
    "identity": IdentityPort,
    "retrieval": RetrievalPort,
    "review_router": ReviewRouterPort,
    "speech_to_text": SpeechToTextPort,
    "text_to_speech": TextToSpeechPort,
    "tool_catalog": ToolCatalogPort,
    "tracer": ObservabilityTracerPort,
    "evaluation": EvaluationGatePort,
}

__all__ = [
    "TokenUsage",
    "ObservabilityTracerPort",
    "EvaluationGatePort",
    "CLIENT_ASSERTED",
    "END_USER_AUTH_ATTR",
    "END_USER_AUTH_KINDS",
    "PORT_PROTOCOLS",
    "UNIMPLEMENTED",
    "VERIFIED",
    "AuditSinkPort",
    "ContactStorePort",
    "ConversationChannelPort",
    "DiarizationPort",
    "EndUserAuthUnavailableError",
    "GenerationPort",
    "GuardrailPort",
    "IdentityPort",
    "RetrievalPort",
    "ReviewRouterPort",
    "SpeechToTextPort",
    "TextToSpeechPort",
    "ToolCatalogPort",
    "declared_end_user_auth",
]
