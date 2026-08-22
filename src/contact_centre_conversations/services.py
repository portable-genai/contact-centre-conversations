"""The composition root for the two mode services: ports in, mode services out.

This is the one place that knows how a container's ports become an
:class:`~.domain.assist_service.AgentAssistService` and a
:class:`~.domain.self_service.SelfServiceService`. Every surface (API, CLI, agent tools, demo,
eval) builds them from here, so the wiring is identical everywhere and a change to it cannot be
half-applied.

It also enforces the ONE thing that is not the domain's business and not an adapter's either:
**a mode route may not be reached unless that mode is enabled.** ``require_mode`` raises
``ModeDisabledError`` (503), and with both flags off every mode route refuses.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Container
from .domain.assist_service import AgentAssistService
from .domain.contact_kernel import ContactKernel
from .domain.guardrails import TurnGuard
from .domain.modes import ContactMode, ModeGate
from .domain.pii import PII_PATTERNS
from .domain.self_service import SelfServiceService

__all__ = ["ModeServices", "build_services", "require_mode"]


@dataclass(frozen=True, slots=True)
class ModeServices:
    """Both mode services plus the kernel they share, built from one container."""

    kernel: ContactKernel
    agent_assist: AgentAssistService
    self_service: SelfServiceService

    def for_mode(self, mode: ContactMode) -> AgentAssistService | SelfServiceService:
        return self.agent_assist if mode is ContactMode.AGENT_ASSIST else self.self_service


def build_services(container: Container) -> ModeServices:
    """Wire the ports into both mode services. No mode check here: see :func:`require_mode`.

    The guard is constructed with the container's guardrail adapter bound as its screen callable,
    which is what makes "redact, then screen, then everything else" a property of the object
    every turn passes through rather than a rule each caller has to remember.
    """
    settings = container.settings
    guardrail = container.guardrail
    guard = TurnGuard(
        PII_PATTERNS,
        lambda text: guardrail.screen(text),
    )
    kernel = ContactKernel(
        store=container.contact_store,
        audit=container.audit,
        guard=guard,
        retrieval=container.retrieval,
        generation=container.generation,
    )
    return ModeServices(
        kernel=kernel,
        agent_assist=AgentAssistService(
            kernel=kernel,
            packs=settings.packs,
            review_router=container.review_router,
            tracer=container.tracer,
        ),
        self_service=SelfServiceService(
            kernel=kernel,
            packs=settings.packs,
            tools=container.tool_catalog,
            review_router=container.review_router,
            tracer=container.tracer,
        ),
    )


def require_mode(container: Container, mode: ContactMode) -> ModeGate:
    """Refuse unless ``mode`` is enabled in this deployment.

    Called by every surface before any work is done, so that a mode nobody enabled cannot be
    reached from the API, the CLI, an agent tool or the demo. With both flags off, every mode
    route refuses, which is the born state: see ``domain/modes.py``.
    """
    return container.settings.modes.require(mode)
