"""Make the backend suite runnable as ONE job (requirement doc s3).

The problem this solves, precisely:

The Mongo-backed test modules each open their own event loop at import time
(``LOOP = asyncio.new_event_loop()``) and close it during module teardown. Motor
binds its client to the first loop it is used on and refuses every later one, so
whichever module ran first won and every module after it raised at setup - 233
errors in a full run, while the very same files passed when run alone. That is a
harness defect, not a product defect, and it hid real regressions: a failure in
the Mongo half of the codebase was indistinguishable from the noise.

The fix is deliberately narrow. It gives every module the SAME loop and stops the
first teardown closing it for everyone else. It does not touch a single
assertion, does not skip anything, and does not mark anything xfail - the suite
still reports exactly what it finds.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# One loop for the whole session, created before any test module is imported.
_SESSION_LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(_SESSION_LOOP)

_real_new_event_loop = asyncio.new_event_loop
_real_close = _SESSION_LOOP.close


def _shared_event_loop() -> asyncio.AbstractEventLoop:
    """Hand every module-level ``asyncio.new_event_loop()`` the session loop.

    Motor pins its IO loop on first use. Sharing one loop is what lets a full
    run behave like the per-file runs the modules were written against.
    """
    return _SESSION_LOOP


def _ignore_close() -> None:
    """A module teardown closing the shared loop would break every later module.

    The loop is closed once, for real, at the end of the session.
    """


asyncio.new_event_loop = _shared_event_loop  # type: ignore[assignment]
_SESSION_LOOP.close = _ignore_close          # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _identity_store_is_in_memory_by_default():
    """The protocol identity gate (P2) builds a MongoDB store lazily from
    ``MONGO_URL`` - which in a developer checkout is the SHARED cluster. A test
    must never reach it by accident, so every test starts with a fresh in-memory
    store; tests that need real MongoDB install their own with ``configure``."""
    import sys
    from pathlib import Path

    backend = str(Path(__file__).resolve().parents[1])
    if backend not in sys.path:
        sys.path.insert(0, backend)
    import protocol_identity_runtime as runtime
    from app.persistence.memory.identity_store import InMemoryIdentityStore

    runtime.configure(store=InMemoryIdentityStore())
    yield
    runtime.configure(store=None)


@pytest.fixture(scope="session", autouse=True)
def _session_event_loop():
    yield
    asyncio.new_event_loop = _real_new_event_loop  # type: ignore[assignment]
    _SESSION_LOOP.close = _real_close              # type: ignore[method-assign]
    try:
        if not _SESSION_LOOP.is_closed():
            _SESSION_LOOP.close()
    except RuntimeError:
        # Already torn down by an interpreter shutdown hook; nothing to do.
        pass
