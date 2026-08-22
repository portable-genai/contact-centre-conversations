"""Shared fixtures for every suite: the REAL local adapters, never bespoke fakes.

The offline implementation must live in exactly one place, so the unit suite drives
``adapters/local`` rather than a parallel set of in-memory doubles that can drift from it. Every
adapter constructs from a single ``Settings`` pointed at ``:memory:``, which is also the adapter
convention the contract suite asserts.

``no_cloud_sdk`` is the portability harness: it makes ``google`` and every submodule
UNIMPORTABLE for the duration of a test, so "the SDK-free profiles construct with no cloud SDK"
is proved by blocking the import rather than by hoping the machine has none installed.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator, Sequence
from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from contact_centre_conversations.adapters.local.contact_store import (
    LocalContactStore,
)
from contact_centre_conversations.config import (
    Container,
    Settings,
    build_container,
    load_packs,
)
from contact_centre_conversations.domain.modes import (
    ModeGates,
)
from contact_centre_conversations.services import (
    ModeServices,
    build_services,
)

from .fixtures import sample_cases
from tests import REPO_ROOT

#: A loopback peer: the app-object exposure guard refuses the unauthenticated local posture to
#: any other peer, and TestClient's default peer is the literal host "testclient".
LOOPBACK_PEER = ("127.0.0.1", 50000)

#: The prefix whose imports the SDK-free proof blocks (every managed SDK this repo lazily uses).
BLOCKED_SDK_ROOTS: tuple[str, ...] = ("google",)

#: The repo's own reviewed policy packs, loaded once. Tests drive the SHIPPED packs rather than
#: bespoke ones, so a pack edit that breaks an engine fails the suite instead of passing against
#: a fixture nobody ships.
SHIPPED_PACKS = load_packs(REPO_ROOT / "config" / "packs")

#: Both gates on, with a promotion bundle each. Written out rather than defaulted: see
#: ``local_settings``.
BOTH_MODES_ON = ModeGates.both_on()


def local_settings(**overrides: Any) -> Settings:
    """``local`` profile settings on ephemeral stores, plus any per-test override.

    BOTH MODES ARE ENABLED here, deliberately and explicitly. The shipped default is both off
    (see ``domain/modes.py``), so a suite that inherited the default would exercise nothing; a
    suite that could not turn them on would not be testing a gate. Naming them here means every
    test that wants the OFF state has to say so, which is the right way round.
    """
    base: dict[str, Any] = {
        "profile": "local",
        "audit_path": ":memory:",
        "tenant": sample_cases.TENANT,
        "kb_path": str(REPO_ROOT / "config" / "kb" / "passages.jsonl"),
        "streams_path": str(REPO_ROOT / "config" / "streams"),
        "packs_path": str(REPO_ROOT / "config" / "packs"),
        "packs": SHIPPED_PACKS,
        "modes": BOTH_MODES_ON,
    }
    base.update(overrides)
    return Settings(**base)


def is_blocked_sdk(fullname: str, roots: Sequence[str] = BLOCKED_SDK_ROOTS) -> bool:
    """True when ``fullname`` is one of the blocked roots or lives under one."""
    return any(fullname == root or fullname.startswith(root + ".") for root in roots)


class _BlockedSdkFinder:
    """A meta-path finder that refuses the blocked roots, whatever is installed."""

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> None:
        if is_blocked_sdk(fullname):
            raise ModuleNotFoundError(
                f"{fullname} is blocked: this profile must construct with no cloud SDK present"
            )
        return None


@pytest.fixture(autouse=True)
def _empty_contact_store() -> Iterator[None]:
    """The offline contact store is process-wide (it models a shared database), so clear it.

    Autouse and unconditional: a test that inherited another test's contacts would pass or fail
    on ordering, and the turn indices would depend on which test ran first.
    """
    LocalContactStore.reset()
    yield
    LocalContactStore.reset()


@pytest.fixture()
def settings() -> Settings:
    return local_settings()


@pytest.fixture()
def container(settings: Settings) -> Container:
    return build_container(settings)


@pytest.fixture()
def services(container: Container) -> ModeServices:
    """Both mode services, wired from the REAL local adapters through the real composition root."""
    return build_services(container)


@pytest.fixture()
def api_client() -> Iterator[TestClient]:
    """The API served to a loopback peer, which is the only posture the local profile allows."""
    from contact_centre_conversations.api.app import app

    with TestClient(app, client=LOOPBACK_PEER) as client:
        yield client


@pytest.fixture()
def no_cloud_sdk() -> Iterator[None]:
    """Make the managed SDKs unimportable, and restore the interpreter afterwards."""
    finder = _BlockedSdkFinder()
    evicted: dict[str, ModuleType] = {
        name: module for name, module in sys.modules.items() if is_blocked_sdk(name)
    }
    for name in evicted:
        del sys.modules[name]
    sys.meta_path.insert(0, finder)  # type: ignore[arg-type]
    try:
        yield
    finally:
        sys.meta_path.remove(finder)  # type: ignore[arg-type]
        sys.modules.update(evicted)


def reimport(module_name: str) -> ModuleType:
    """Import ``module_name`` from scratch, so an import-time failure is actually observed."""
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)
