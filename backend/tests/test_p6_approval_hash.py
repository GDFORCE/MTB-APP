"""P6-01: the approval hash is stamped in the guarded approval write, bound to the content
it was computed from, protected at the storage boundary, and handled honestly for versions
approved before it existed."""

from __future__ import annotations

import threading
from uuid import UUID, uuid4

import pytest

from app.application.amendment_common import load_version
from app.application.amendment_service import AmendmentService
from app.application.authoring import (
    ProtocolRecord, ProtocolVersionRecord, ScheduleDefinitionRecord, TrialRecord,
)
from app.application.errors import (
    AmendmentBlockedError, AmendmentIntegrityError, ImmutableRecordError, StaleRevisionError,
)
from app.application.pilot import build_pilot_amendment_schedule, build_pilot_schedule
from app.domain.schedule.amendment import (
    CONTENT_HASH_VERSION, HashStatus, header_integrity_hash, schedule_content_hash,
    verify_content_hash,
)
from app.domain.schedule.models import ScheduleStatus

from _p6_world import World

pytest_plugins = ["tests.contract.conftest"]

REVIEWER = uuid4()


class Draft:
    """An IN_REVIEW version (not yet approved), written straight through the ports."""

    def __init__(self, uow, *, org=None, trial=None, protocol=None, label="1.0", builder=build_pilot_schedule):
        self.uow = uow
        self.org = org or uuid4()

        def setup(tx):
            trial_record = TrialRecord(organization_id=self.org) if trial is None else trial
            if trial is None:
                tx.authoring.add_trial(trial_record)
            protocol_record = protocol or ProtocolRecord(
                organization_id=self.org, trial_id=trial_record.id, protocol_number=f"P-{uuid4().hex[:6]}")
            if protocol is None:
                tx.authoring.add_protocol(protocol_record)
            version = ProtocolVersionRecord(
                organization_id=self.org, trial_id=trial_record.id, protocol_id=protocol_record.id,
                version_label=label, document_name="p.pdf", document_uri="private://p.pdf",
                document_hash=uuid4().hex * 2)
            tx.authoring.add_protocol_version(version)
            definition = ScheduleDefinitionRecord(
                organization_id=self.org, trial_id=trial_record.id, protocol_id=protocol_record.id,
                protocol_version_id=version.id, name="Primary")
            tx.authoring.add_definition(definition)
            schedule = builder(version.id, "Primary")
            schedule.schedule_metadata.status = ScheduleStatus.DRAFT
            tx.schedules.persist_draft(schedule, schedule_definition_id=definition.id)
            tx.schedules.transition(schedule.schedule_version_id, to_status="IN_REVIEW")
            return trial_record, protocol_record, schedule.schedule_version_id

        self.trial, self.protocol, self.version = uow.run(setup)

    def header(self):
        return self.uow.read(lambda tx: tx.schedules.get_header(self.version))

    def schedule(self):
        return self.uow.read(lambda tx: tx.schedules.get(self.version))


def test_a_direct_approval_still_stamps_the_hash_and_the_header_integrity(uow):
    draft = Draft(uow)
    approved = uow.run(lambda tx: tx.schedules.mark_approved(
        draft.version, reviewer_id=REVIEWER, approved_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc), effective_from=None))
    assert approved.content_hash_version == CONTENT_HASH_VERSION
    assert approved.content_hash == schedule_content_hash(draft.schedule())
    assert approved.header_integrity_hash
    loaded = uow.read(lambda tx: load_version(tx, draft.org, draft.version))
    assert loaded.hash_status == HashStatus.VERIFIED


def test_a_version_cannot_be_approved_through_a_plain_transition(uow):
    draft = Draft(uow)
    with pytest.raises(ImmutableRecordError, match="mark_approved"):
        uow.run(lambda tx: tx.schedules.transition(draft.version, to_status="APPROVED"))
    assert draft.header().status == "IN_REVIEW" and draft.header().content_hash is None


def test_an_approval_that_hashed_older_content_cannot_commit(uow):
    from datetime import datetime, timezone
    draft = Draft(uow)
    header, schedule = draft.header(), draft.schedule()
    stale_hash = schedule_content_hash(schedule)
    event = next(item for item in schedule.events if item.code == "C1D1")
    edited = event.model_copy(deep=True)
    edited.display_name = "edited after the approver read the content"
    uow.run(lambda tx: tx.schedules.replace_event(draft.version, edited))
    assert draft.header().revision == header.revision + 1             # a content edit moves it
    with pytest.raises(StaleRevisionError):
        uow.run(lambda tx: tx.schedules.mark_approved(
            draft.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc),
            effective_from=None, content_hash=stale_hash, content_hash_version=CONTENT_HASH_VERSION,
            header_integrity_hash="x" * 64, expected_revision=header.revision))
    assert draft.header().status == "IN_REVIEW"                        # nothing was approved


def test_an_approved_version_cannot_be_edited_or_re_parented_or_re_approved(uow):
    from datetime import datetime, timezone
    draft = Draft(uow)
    uow.run(lambda tx: tx.schedules.mark_approved(
        draft.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc), effective_from=None))
    before = draft.header()
    event = draft.schedule().events[0].model_copy(deep=True)
    event.display_name = "tampered"
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.replace_event(draft.version, event))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.add_event(draft.version, event.model_copy(update={"id": uuid4(), "code": "NEW"})))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.set_lineage(draft.version, uuid4()))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.mark_approved(
            draft.version, reviewer_id=uuid4(), approved_at=datetime.now(timezone.utc), effective_from=None))
    with pytest.raises(ImmutableRecordError):
        uow.run(lambda tx: tx.schedules.record_material_edit(draft.version, editor_id=uuid4(),
                                                             at=datetime.now(timezone.utc)))
    assert draft.header() == before
    assert verify_content_hash(draft.schedule(), before.content_hash, before.content_hash_version) \
        == HashStatus.VERIFIED


def test_a_tampered_approval_header_is_detected_even_though_the_content_hash_is_fine(uow):
    """effective_from is excluded from the content hash; the header integrity hash covers it."""
    from datetime import date, datetime, timezone
    draft = Draft(uow)
    uow.run(lambda tx: tx.schedules.mark_approved(
        draft.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc),
        effective_from=date(2027, 1, 1)))
    header = draft.header()
    assert uow.read(lambda tx: load_version(tx, draft.org, draft.version)).hash_status == HashStatus.VERIFIED
    if hasattr(uow, "_database"):
        uow._database["schedule_versions"].update_one(
            {"_id": draft.version}, {"$set": {"effective_from": "2026-01-01"}})
    else:
        uow._store.versions[draft.version]["header"] = header.model_copy(update={
            "effective_from": date(2026, 1, 1)})
    with pytest.raises(AmendmentIntegrityError, match="approval header"):
        uow.read(lambda tx: load_version(tx, draft.org, draft.version))
    # ... while the content hash alone would not have noticed
    assert verify_content_hash(draft.schedule(), header.content_hash, header.content_hash_version) \
        == HashStatus.VERIFIED


def test_header_integrity_binds_lineage_approver_and_time(uow):
    from datetime import datetime, timezone
    draft = Draft(uow)
    at = datetime.now(timezone.utc)
    approved = uow.run(lambda tx: tx.schedules.mark_approved(
        draft.version, reviewer_id=REVIEWER, approved_at=at, effective_from=None))
    expected = header_integrity_hash(
        schedule_version_id=approved.id, organization_id=approved.organization_id,
        trial_id=approved.trial_id, protocol_id=approved.protocol_id,
        protocol_version_id=approved.protocol_version_id,
        schedule_definition_id=approved.schedule_definition_id, version_number=approved.version_number,
        based_on_schedule_version_id=approved.based_on_schedule_version_id, effective_from=None,
        approved_by=REVIEWER, approved_at=approved.approved_at, content_hash=approved.content_hash,
        content_hash_version=approved.content_hash_version)
    assert approved.header_integrity_hash == expected


# --------------------------------------------------------------- historical, hashless versions
def make_legacy(uow, version: UUID) -> None:
    """Put an approved version back in the state it had BEFORE hashes existed."""
    if hasattr(uow, "_database"):
        uow._database["schedule_versions"].update_one(
            {"_id": version},
            {"$set": {"content_hash": None, "content_hash_version": None, "header_integrity_hash": None}},
            bypass_document_validation=True)
    else:
        row = uow._store.versions[version]
        row["header"] = row["header"].model_copy(update={
            "content_hash": None, "content_hash_version": None, "header_integrity_hash": None})


def test_a_hashless_historical_version_is_never_declared_verified_or_rewritten(uow):
    world = World(uow)
    make_legacy(uow, world.v1)
    before = uow.read(lambda tx: tx.schedules.get(world.v1))
    loaded = uow.read(lambda tx: load_version(tx, world.org, world.v1))
    assert loaded.hash_status == HashStatus.UNVERIFIED_LEGACY
    assert loaded.identity()["verification"] == "UNVERIFIED_LEGACY" and loaded.header.content_hash is None
    # nothing was written to manufacture a hash, and the content is untouched
    assert uow.read(lambda tx: tx.schedules.get_header(world.v1)).content_hash is None
    assert uow.read(lambda tx: tx.schedules.get(world.v1)).model_dump(mode="json") == before.model_dump(mode="json")


def test_a_legacy_earlier_version_is_usable_with_a_warning_but_a_legacy_later_one_is_not(uow):
    world = World(uow, reviewed=False)
    # the pair prepared BEFORE the version lost its hash, so rebuild the world with a legacy V1
    uow2 = uow
    legacy_world = World(uow2)
    make_legacy(uow2, legacy_world.v1)
    view = AmendmentService(uow2).pair_view(
        organization_id=legacy_world.org, pair_id=UUID(legacy_world.pair["pair_id"]))
    assert view["from"]["verification"] == "UNVERIFIED_LEGACY" and view["to"]["verification"] == "VERIFIED"
    legacy_world.review_all()
    simulation = legacy_world.simulate()
    assert simulation["can_confirm"], simulation["blockers"]
    assert [w["code"] for w in simulation["warnings"]] == ["VERSION_UNVERIFIED_LEGACY"]
    assert simulation["from"]["verification"] == "UNVERIFIED_LEGACY"
    # a hashless TARGET cannot be adopted: nothing proves it is what was approved
    make_legacy(uow2, legacy_world.v2)
    blocked = legacy_world.simulate()
    assert not blocked["can_confirm"]
    assert "VERSION_UNVERIFIED_LEGACY" in {b["code"] for b in blocked["blockers"]}
    assert world.org != legacy_world.org


def test_a_mismatching_hash_stops_everything(uow):
    world = World(uow, reviewed=True)
    row = uow._store.versions[world.v2] if hasattr(uow, "_store") else None
    if row is not None:
        row["header"] = row["header"].model_copy(update={"content_hash": "0" * 64})
    else:
        uow._database["schedule_versions"].update_one({"_id": world.v2}, {"$set": {"content_hash": "0" * 64}})
    with pytest.raises(AmendmentIntegrityError):
        world.simulate()
    with pytest.raises(AmendmentIntegrityError):
        AmendmentService(uow).pair_view(organization_id=world.org, pair_id=UUID(world.pair["pair_id"]))
    assert uow.read(lambda tx: tx.patients.get(world.org, world.patient_ids[0])).current_schedule_version_id == world.v1


def test_lineage_is_checked_before_a_version_is_approved(uow):
    draft = Draft(uow)
    parent = Draft(uow, org=draft.org, trial=draft.trial, protocol=draft.protocol, label="0.9")
    from datetime import datetime, timezone
    uow.run(lambda tx: tx.schedules.mark_approved(
        parent.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc), effective_from=None))
    service = AmendmentService(uow)
    result = service.declare_lineage(
        organization_id=draft.org, actor_id=REVIEWER, schedule_version_id=draft.version,
        based_on_schedule_version_id=parent.version, reason="amendment 1")
    assert result["based_on_schedule_version_id"] == str(parent.version)
    again = service.declare_lineage(
        organization_id=draft.org, actor_id=REVIEWER, schedule_version_id=draft.version,
        based_on_schedule_version_id=parent.version, reason="idempotent")
    assert again == result
    approved = uow.run(lambda tx: tx.schedules.mark_approved(
        draft.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc), effective_from=None))
    assert approved.based_on_schedule_version_id == parent.version
    loaded = uow.read(lambda tx: load_version(tx, draft.org, draft.version))
    assert loaded.hash_status == HashStatus.VERIFIED                   # lineage is inside the integrity hash
    with pytest.raises(AmendmentBlockedError) as raised:               # too late once approved
        service.declare_lineage(
            organization_id=draft.org, actor_id=REVIEWER, schedule_version_id=parent.version,
            based_on_schedule_version_id=draft.version, reason="reverse")
    assert raised.value.blockers


# --------------------------------------------------------------------- real MongoDB only
def test_the_store_itself_refuses_an_approved_version_without_a_hash(mongo_uow):
    from pymongo.errors import OperationFailure
    draft = Draft(mongo_uow)
    collection = mongo_uow._database["schedule_versions"]
    with pytest.raises(OperationFailure) as raised:
        collection.update_one({"_id": draft.version}, {"$set": {"status": "APPROVED"}})
    assert raised.value.code == 121                                    # DocumentValidationFailure
    with pytest.raises(OperationFailure):
        collection.update_one({"_id": draft.version}, {"$set": {"content_hash": 7}})
    assert collection.find_one({"_id": draft.version})["status"] == "IN_REVIEW"
    options = mongo_uow._database.command("listCollections", filter={"name": "schedule_versions"})
    assert options["cursor"]["firstBatch"][0]["options"]["validationLevel"] == "moderate"


def test_migration_nine_is_idempotent_and_resumable(mongo_uow):
    from app.persistence.mongodb import migrate
    from app.persistence.mongodb.migrations import __path__ as _  # noqa: F401
    database = mongo_uow._database
    assert migrate.run_migrations(database) == []
    ledger = database[migrate.LEDGER]
    ledger.update_one({"version": 9}, {"$set": {"status": "RUNNING"}})
    assert migrate.run_migrations(database) == [9]
    assert ledger.find_one({"version": 9})["status"] == "COMPLETED"
    for name, key in (("amendment_pairs", "uq_pair_versions"), ("amendment_mapping_sets", "uq_mapping_set_pair"),
                      ("amendment_decisions", "uq_decision_open_patient"),
                      ("amendment_decisions", "uq_decision_idempotency")):
        assert key in database[name].index_information()
    info = database["amendment_decisions"].index_information()["uq_decision_open_patient"]
    assert info["unique"] and info["partialFilterExpression"] == {"is_open": True}
    assert info["key"] == [("organization_id", 1), ("patient_id", 1)]


def test_concurrent_approval_and_edit_never_leave_a_hash_for_other_content(mongo_uow):
    """Approve and edit the same draft at the same moment, many times. Whatever order the
    store serialises them in, an approved version's hash is always the hash of its content."""
    from datetime import datetime, timezone
    from concurrent.futures import ThreadPoolExecutor

    outcomes = {"approved_first": 0, "edit_first": 0}
    for round_ in range(8):
        draft = Draft(mongo_uow)
        event = draft.schedule().events[0].model_copy(deep=True)
        event.display_name = f"concurrent edit {round_}"
        gate = threading.Barrier(2)

        def approve():
            gate.wait()
            try:
                return mongo_uow.run(lambda tx: tx.schedules.mark_approved(
                    draft.version, reviewer_id=REVIEWER, approved_at=datetime.now(timezone.utc),
                    effective_from=None))
            except Exception as error:                                  # noqa: BLE001
                return error

        def edit():
            gate.wait()
            try:
                mongo_uow.run(lambda tx: tx.schedules.replace_event(draft.version, event))
                return "edited"
            except Exception as error:                                  # noqa: BLE001
                return error

        with ThreadPoolExecutor(2) as pool:
            futures = (pool.submit(approve), pool.submit(edit))       # both in flight before either waits
            approval, edit_result = futures[0].result(), futures[1].result()
        header, schedule = draft.header(), draft.schedule()
        if header.status == "APPROVED":
            assert verify_content_hash(schedule, header.content_hash, header.content_hash_version) \
                == HashStatus.VERIFIED, (round_, approval, edit_result)
            outcomes["approved_first" if isinstance(edit_result, ImmutableRecordError) else "edit_first"] += 1
        else:
            assert isinstance(approval, Exception) and edit_result == "edited"
    assert sum(outcomes.values()) + 0 <= 8


def test_the_amendment_pilot_builders_are_independent_artefacts():
    a = build_pilot_schedule(uuid4(), "Primary")
    b = build_pilot_amendment_schedule(uuid4(), "Primary")
    assert a.schedule_version_id != b.schedule_version_id
    assert {e.id for e in a.events}.isdisjoint({e.id for e in b.events})
    assert schedule_content_hash(a) != schedule_content_hash(b)


def test_the_amendment_collections_have_real_validators_and_indexes(mongo_uow):
    """Malformed amendment documents are refused by the STORE, not only by the services."""
    from pymongo.errors import DuplicateKeyError, OperationFailure
    from app.persistence.mongodb.documents import to_document
    from tests.contract.test_amendment_contract import decision, mapping_set, pair, simulation

    database = mongo_uow._database
    good = to_document(decision(uuid4()))
    database["amendment_decisions"].insert_one({**good, "_id": uuid4()})          # a valid one is accepted
    for bad in (
            {**good, "_id": uuid4(), "status": "PENDING"},                          # not a decision status
            {**good, "_id": uuid4(), "decision_type": "MIGRATE"},                   # not a decision type
            {**good, "_id": uuid4(), "is_open": "yes"},                             # wrong type
            {k: v for k, v in good.items() if k not in {"reason", "_id"}} | {"_id": uuid4()},   # missing field
            {**good, "_id": uuid4(), "patient_id": "not-a-uuid"}):
        with pytest.raises(OperationFailure) as raised:
            database["amendment_decisions"].insert_one(bad)
        assert raised.value.code == 121
    record = pair()
    database["amendment_pairs"].insert_one(to_document(record))
    with pytest.raises(DuplicateKeyError):                                         # one pair per ordered versions
        database["amendment_pairs"].insert_one({**to_document(record), "_id": uuid4()})
    with pytest.raises(OperationFailure):
        database["amendment_pairs"].insert_one({**to_document(pair()), "_id": uuid4(), "change_set": "x"})
    sim = to_document(simulation(uuid4()))
    with pytest.raises(OperationFailure):
        database["amendment_simulations"].insert_one({**sim, "_id": uuid4(), "status": "NEW"})
    ms = to_document(mapping_set(record))
    database["amendment_mapping_sets"].insert_one(ms)
    with pytest.raises(DuplicateKeyError):
        database["amendment_mapping_sets"].insert_one({**ms, "_id": uuid4()})
