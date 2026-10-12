"""In-memory fakes of the repository ports, for fast unit tests.

Held to the same contract suite as the MongoDB adapters (``tests/contract``),
so a fake can never drift into behaviour the real store would not have.
"""

from .unit_of_work import InMemoryUnitOfWork

__all__ = ["InMemoryUnitOfWork"]
