"""P12-03: a run executes only with provenance, its trace stays bounded, and every live queue entry names its versions."""
import ast
from pathlib import Path
from uuid import uuid4

import pytest

from app.application.authoring import ProtocolRecord, ProtocolVersionRecord, TrialRecord
from app.application.extraction import MAX_TRACE_BYTES, ExtractionService, ProvenanceError
from app.domain.schedule.models import UniversalSchedule
from app.extraction.contracts import ExtractionResult

pytest_plugins = ["tests.contract.conftest"]
BACKEND = Path(__file__).resolve().parents[1]


def seeded(uow):
    org = uuid4()
    trial = TrialRecord(organization_id=org, external_trial_id=f"t-{uuid4().hex[:6]}")
    protocol = ProtocolRecord(organization_id=org, trial_id=trial.id, protocol_number="ABC-1")
    version = ProtocolVersionRecord(organization_id=org, trial_id=trial.id, protocol_id=protocol.id, version_label="1",
                                    document_name="p.pdf", document_uri="private://p.pdf", document_hash="a" * 64)

    def seed(tx):
        tx.authoring.add_trial(trial); tx.authoring.add_protocol(protocol); tx.authoring.add_protocol_version(version)
    uow.run(seed)
    return org, version


def queue(uow, org, version, **override):
    args = dict(provider="anthropic", model_name="claude-model", model_version="1", prompt_version="uctsm-extractor.v1")
    args.update(override)
    return ExtractionService(uow).queue(organization_id=org, protocol_version_id=version.id, idempotency_key=None, **args)


@pytest.mark.parametrize("override, missing", [
    ({"provider": None}, "provider"), ({"provider": "  "}, "provider"), ({"model_name": None}, "model_name"),
    ({"prompt_version": " "}, "prompt_version"),
])
def test_a_run_without_provider_model_or_prompt_cannot_start(uow, override, missing):
    org, version = seeded(uow)
    run = queue(uow, org, version, **override)
    with pytest.raises(ProvenanceError, match=missing):
        ExtractionService(uow).start(run.id)
    assert uow.read(lambda tx: tx.authoring.get_run(run.id)).status == "QUEUED"          # never claimed, never ran


def test_a_run_with_full_provenance_starts_and_keeps_the_versions_it_ran_under(uow):
    org, version = seeded(uow)
    run = queue(uow, org, version)
    started = ExtractionService(uow).start(run.id)
    assert started.status == "RUNNING"
    assert (started.provider, started.model_name, started.model_version, started.prompt_version, started.schema_version) == (
        "anthropic", "claude-model", "1", "uctsm-extractor.v1", "uctsm.v1")


def test_an_oversized_trace_is_refused_so_raw_provider_payloads_cannot_bloat_the_run(uow):
    org, version = seeded(uow)
    service = ExtractionService(uow)
    run = queue(uow, org, version)
    started = service.start(run.id)
    huge = ExtractionResult(schedule=None, issues=[], extraction_trace=[{"raw_response": "x" * (MAX_TRACE_BYTES + 10)}])
    with pytest.raises(ValueError, match="artifact store"):
        service.complete_many(run.id, [(None, huge)], claim_token=started.lease_token)
    assert uow.read(lambda tx: tx.authoring.get_run(run.id)).status == "RUNNING"          # nothing half-written


def test_every_live_queue_entry_point_names_provider_model_and_prompt():
    """Source audit: each call that queues a run passes all four provenance values (an imported schedule passes
    its sentinel provider and prompt constants)."""
    required = {"provider", "model_name", "prompt_version"}
    audited = []
    for path in [*BACKEND.glob("app/**/*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"queue", "queue_in"}:
                keywords = {item.arg for item in node.keywords if item.arg}
                if "protocol_version_id" in keywords or "protocol_version" in keywords:
                    audited.append(path.name)
                    assert required <= keywords, f"{path.name}:{node.lineno} queues a run without {required - keywords}"
    assert len(audited) >= 4                                                              # the scan really found the entries
