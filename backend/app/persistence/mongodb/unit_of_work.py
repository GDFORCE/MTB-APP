"""MongoDB unit of work: one replica-set transaction per business operation.

``run`` uses PyMongo's ``with_transaction`` convenience API, which retries the
callback on ``TransientTransactionError`` and the commit on
``UnknownTransactionCommitResult``. Consequently the callback is re-run from the
start: it must be deterministic and free of external side effects (the pure
evaluator, repository writes, and outbox rows are all fine).
"""

from __future__ import annotations

from typing import Callable, TypeVar

from pymongo import MongoClient
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

from .amendment_repository import MongoAmendmentRepository
from .audit_repository import MongoAuditRepository
from .authoring_repository import MongoAuthoringRepository
from .patient_repository import (
    MongoCommandRepository,
    MongoOutboxRepository,
    MongoEvaluationRepository,
    MongoImpactRepository,
    MongoParityRepository,
    MongoPatientFactsRepository,
    MongoPatientRepository,
)
from .command_repository import MongoPatientCommandRepository
from .schedule_repository import MongoScheduleVersionRepository

T = TypeVar("T")

_READ_CONCERN = ReadConcern("majority")
_WRITE_CONCERN = WriteConcern("majority")


class BoundUnitOfWork:
    """The repositories handed to a callback, all bound to one session."""

    def __init__(self, database: Database, session: ClientSession | None):
        self.session = session
        self.audit = MongoAuditRepository(database, session)
        self.authoring = MongoAuthoringRepository(database, session)
        self.schedules = MongoScheduleVersionRepository(database, session)
        self.patients = MongoPatientRepository(database, session)
        self.facts = MongoPatientFactsRepository(database, session)
        self.evaluations = MongoEvaluationRepository(database, session)
        self.commands = MongoCommandRepository(database, session)
        self.ledger = MongoPatientCommandRepository(database, session)
        self.impacts = MongoImpactRepository(database, session)
        self.parity = MongoParityRepository(database, session)
        self.outbox = MongoOutboxRepository(database, session)
        self.amendments = MongoAmendmentRepository(database, session)

    def run(self, fn):  # pragma: no cover - guard
        raise RuntimeError("nested units of work are not allowed")

    read = run


class MongoUnitOfWork:
    def __init__(self, client: MongoClient, database: Database):
        self._client = client
        self._database = database

    def run(self, fn: Callable[[BoundUnitOfWork], T]) -> T:
        with self._client.start_session() as session:
            return session.with_transaction(
                lambda active: fn(BoundUnitOfWork(self._database, active)),
                read_concern=_READ_CONCERN,
                write_concern=_WRITE_CONCERN,
                read_preference=ReadPreference.PRIMARY,
            )

    def read(self, fn: Callable[[BoundUnitOfWork], T]) -> T:
        return fn(BoundUnitOfWork(self._database, None))
