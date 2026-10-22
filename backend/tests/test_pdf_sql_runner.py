"""Characterize the retained SQL worker; SQLite does not prove PostgreSQL locking."""
import asyncio
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models as db
from app.domain.schedule.models import UniversalSchedule
from app.extraction import runner
from app.extraction.contracts import ExtractionResult
from app.services.extraction_service import ExtractionService
from test_mongo_parity_extraction import ORG, SqlStack, definition, native_schedule


def test_duplicate_sql_worker_does_not_mark_the_winning_attempt_failed(monkeypatch):
    stack = SqlStack()
    stack.native([definition("existing-synthetic-draft")])
    protocol_version = stack.session.scalar(select(db.ProtocolVersion))
    queued = ExtractionService(stack.session).queue(organization_id=ORG, protocol_version=protocol_version,
        idempotency_key=None, provider="synthetic", model_name="test", model_version="1", prompt_version="1")
    stack.session.commit()
    run_id = queued.id
    entered, release = threading.Event(), threading.Event()
    async def load(_uri): return [], None
    async def identity(**_kwargs): return None
    def extract(*_args, **_kwargs):
        entered.set()
        assert release.wait(10)
        return [(None, ExtractionResult(schedule=UniversalSchedule.model_validate(native_schedule()),
                                        issues=[], extraction_trace=[]))]
    monkeypatch.setattr(runner, "load_pages", load)
    monkeypatch.setattr(runner, "_clear_identity", identity)
    monkeypatch.setattr(runner, "extract_schedules", extract)
    monkeypatch.setattr(runner, "storage_artifacts", lambda: None)
    with Session(stack.session.bind) as second:
        async def scenario():
            first = asyncio.create_task(runner.execute_run(stack.session, run_id))
            assert await asyncio.to_thread(entered.wait, 5)
            try:
                duplicate = await runner.execute_run(second, run_id)
            finally:
                release.set()
            return await first, duplicate
        winner, duplicate = asyncio.run(scenario())
    assert winner and duplicate is None
    stack.session.expire_all()
    assert stack.session.get(db.ExtractionRun, run_id).status == "COMPLETED"
    created = list(stack.session.scalars(select(db.ScheduleVersion).where(db.ScheduleVersion.extraction_run_id == run_id)))
    assert [row.id for row in created] == winner
    engine = stack.session.bind
    stack.session.close(); engine.dispose()
