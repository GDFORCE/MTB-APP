"""The extraction worker on the repository ports: success, failure, unavailable."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.application.authoring import ProtocolRecord, ProtocolVersionRecord, TrialRecord
from app.application.extraction import ExtractionService
from app.domain.schedule.models import UniversalSchedule
from app.extraction import runner
from app.extraction.contracts import DocumentPage, ExtractionResult
from app.extraction.llm_client import ExtractionNotConfigured
from test_mongo_parity_extraction import native_schedule

pytest_plugins = ["tests.contract.conftest"]

ORG = UUID("55555555-5555-4555-8555-555555555555")


@pytest.fixture
def queued(uow):
    trial = TrialRecord(organization_id=ORG, external_trial_id=f"t-{uuid4().hex[:6]}")
    protocol = ProtocolRecord(organization_id=ORG, trial_id=trial.id, protocol_number="ABC-1")
    version = ProtocolVersionRecord(
        organization_id=ORG, trial_id=trial.id, protocol_id=protocol.id, version_label="1",
        document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64)

    def seed(tx):
        tx.authoring.add_trial(trial)
        tx.authoring.add_protocol(protocol)
        tx.authoring.add_protocol_version(version)

    uow.run(seed)
    run = ExtractionService(uow).queue(
        organization_id=ORG, protocol_version_id=version.id, idempotency_key=None,
        provider="test", model_name="m", model_version="1", prompt_version="p1")
    return uow, run, version


#: The stored document states the protocol number the version was created with,
#: so it passes the worker's identity gate (P2-02).
IDENTIFIED = [DocumentPage(
    page_number=1, text="PROTOCOL\nProtocol Number: ABC-1\nSchedule of Assessments follows.")]


def fake_pages(monkeypatch, *, results=None, error=None, pages=None):
    calls = {"extract": 0}

    async def load(_uri):
        return (IDENTIFIED if pages is None else pages), None

    def extract(*_args, **_kwargs):
        calls["extract"] += 1
        if error is not None:
            raise error
        return results

    monkeypatch.setattr(runner, "load_pages", load)
    monkeypatch.setattr(runner, "extract_schedules", extract)
    monkeypatch.setattr(runner, "storage_artifacts", lambda: None)
    return calls


def test_a_successful_run_completes_and_returns_the_new_draft_versions(queued, monkeypatch):
    uow, run, _version = queued
    schedule = UniversalSchedule.model_validate(native_schedule())
    fake_pages(monkeypatch, results=[(None, ExtractionResult(
        schedule=schedule, issues=[], extraction_trace=[]))])

    version_ids = asyncio.run(runner.execute_run_on_ports(uow, run.id))

    assert version_ids and len(version_ids) == 1
    stored = uow.read(lambda tx: tx.authoring.get_run(run.id))
    assert stored.status == "COMPLETED"
    header = uow.read(lambda tx: tx.schedules.get_header(version_ids[0]))
    assert header.extraction_run_id == run.id


def test_a_failing_engine_marks_the_run_failed_and_creates_no_schedule(queued, monkeypatch):
    uow, run, _version = queued
    fake_pages(monkeypatch, error=RuntimeError("model exploded"))

    assert asyncio.run(runner.execute_run_on_ports(uow, run.id)) is None

    stored = uow.read(lambda tx: tx.authoring.get_run(run.id))
    assert stored.status == "FAILED"
    assert stored.error_details["type"] == "RuntimeError"
    assert "model exploded" in stored.error_details["message"]
    headers = uow.read(lambda tx: tx.schedules.list_headers(organization_id=ORG))
    assert headers == []


def test_an_unconfigured_engine_fails_the_run_with_the_reason(queued, monkeypatch):
    uow, run, _version = queued
    monkeypatch.delenv("UCTSM_EXTRACTION_PROVIDER", raising=False)
    fake_pages(monkeypatch, error=ExtractionNotConfigured(runner.ENGINE_UNAVAILABLE_MESSAGE))

    assert asyncio.run(runner.execute_run_on_ports(uow, run.id)) is None

    stored = uow.read(lambda tx: tx.authoring.get_run(run.id))
    assert stored.status == "FAILED"
    assert "not configured" in stored.error_details["message"]


@pytest.mark.parametrize("pages,issue", [
    ([DocumentPage(page_number=1, text="PROTOCOL\nProtocol Number: OTHER-9\nbody " * 4)],
     "EXPECTED_IDENTIFIER_CONFLICT"),
    ([DocumentPage(page_number=1, text="A study with no stated identity. " * 6)],
     "EXPECTED_IDENTIFIER_NOT_STATED"),
    ([DocumentPage(page_number=1, text="", text_status="image_or_empty")], "NO_USABLE_TEXT"),
])
def test_a_document_that_fails_identity_never_reaches_the_extraction_engine(
        queued, monkeypatch, pages, issue):
    uow, run, _version = queued
    calls = fake_pages(monkeypatch, results=[], pages=pages)

    assert asyncio.run(runner.execute_run_on_ports(uow, run.id)) is None

    assert calls["extract"] == 0                         # no model call, no compile
    stored = uow.read(lambda tx: tx.authoring.get_run(run.id))
    assert stored.status == "FAILED"
    assert "ProtocolIdentityBlocked" in stored.error_details["message"]
    assert uow.read(lambda tx: tx.schedules.list_headers(organization_id=ORG)) == []
    import protocol_identity_runtime as gate
    blocked = gate.service().list_for_review(ORG)
    assert any(issue in [i.code for i in row.issues] and row.stage == "WORKER" for row in blocked)


def test_a_document_that_states_the_expected_protocol_passes_the_worker_gate(queued, monkeypatch):
    uow, run, _version = queued
    schedule = UniversalSchedule.model_validate(native_schedule())
    calls = fake_pages(monkeypatch, results=[(None, ExtractionResult(
        schedule=schedule, issues=[], extraction_trace=[]))])
    assert asyncio.run(runner.execute_run_on_ports(uow, run.id))
    assert calls["extract"] == 1


def test_an_unknown_run_is_reported_not_swallowed(queued):
    uow, _run, _version = queued
    with pytest.raises(KeyError, match="extraction run not found"):
        asyncio.run(runner.execute_run_on_ports(uow, uuid4()))
