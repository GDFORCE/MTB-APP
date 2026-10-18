"""P6-01/02/03 on the repository ports, against the in-memory fake AND real MongoDB
(the ``uow`` fixture runs every test on both)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.application.amendment_service import AmendmentService
from app.application.errors import (
    AmendmentBlockedError, AmendmentIntegrityError, RecordNotFoundError, StaleRevisionError,
)
from app.application.pilot import PilotService
from app.domain.schedule.amendment import (
    CONTENT_HASH_VERSION, HashStatus, MappingReviewStatus, schedule_content_hash,
    verify_content_hash,
)

pytest_plugins = ["tests.contract.conftest"]       # the memory + real-Mongo ``uow`` fixture


class World:
    def __init__(self, uow):
        self.uow = uow
        self.org, self.editor, self.approver, self.reviewer = uuid4(), uuid4(), uuid4(), uuid4()
        pilot = PilotService(uow)
        self.base = pilot.seed(organization_id=self.org, editor_id=self.editor,
                               approver_id=self.approver)
        self.trial_id = self.base["trial_id"]
        self.amended = pilot.seed_amendment(
            organization_id=self.org, editor_id=self.editor, approver_id=self.approver)
        from uuid import UUID
        self.v1 = UUID(self.amended["from_schedule_version_id"])
        self.v2 = UUID(self.amended["to_schedule_version_id"])
        self.service = AmendmentService(uow)

    def header(self, version):
        return self.uow.read(lambda tx: tx.schedules.get_header(version))


@pytest.fixture()
def world(uow):
    return World(uow)


def test_approval_stamps_the_hash_in_the_approval_write(world):
    for version in (world.v1, world.v2):
        header = world.header(version)
        assert header.status == "APPROVED"
        assert header.content_hash and header.content_hash_version == CONTENT_HASH_VERSION
        assert header.header_integrity_hash
        schedule = world.uow.read(lambda tx, v=version: tx.schedules.get(v))
        assert verify_content_hash(schedule, header.content_hash, header.content_hash_version) \
            == HashStatus.VERIFIED
        assert schedule_content_hash(schedule) == header.content_hash     # persisted reconstruction


def test_v1_is_unchanged_after_v2_is_approved(world):
    before = world.header(world.v1)
    assert before.status == "APPROVED" and before.based_on_schedule_version_id is None
    assert world.header(world.v2).based_on_schedule_version_id == world.v1
    # seeding the amendment again changes nothing
    PilotService(world.uow).seed_amendment(
        organization_id=world.org, editor_id=world.editor, approver_id=world.approver)
    assert world.header(world.v1) == before


def test_lineage_is_fixed_once_declared_and_validated(world):
    from app.application.errors import ImmutableRecordError
    with pytest.raises(AmendmentBlockedError) as raised:
        world.service.declare_lineage(
            organization_id=world.org, actor_id=world.editor, schedule_version_id=world.v1,
            based_on_schedule_version_id=world.v2, reason="try to reverse it")
    assert raised.value.blockers[0]["code"] in {
        "VERSION_NOT_APPROVED", "LINEAGE_AFTER_APPROVAL", "LINEAGE_SAME_PROTOCOL_VERSION",
        "LINEAGE_CYCLE", "LINEAGE_FIXED"}
    with pytest.raises(AmendmentBlockedError):
        world.service.declare_lineage(
            organization_id=world.org, actor_id=world.editor, schedule_version_id=world.v2,
            based_on_schedule_version_id=world.v2, reason="self")
    with pytest.raises(ImmutableRecordError):
        world.uow.run(lambda tx: tx.schedules.set_lineage(world.v2, uuid4()))


def test_pair_is_prepared_once_with_semantic_changes_and_a_server_baseline(world):
    first = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    again = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    assert first["pair_id"] == again["pair_id"]
    assert first["from"]["verification"] == "VERIFIED" and first["to"]["verification"] == "VERIFIED"
    assert first["from"]["content_hash"] != first["to"]["content_hash"]
    kinds = {item["change_type"] for item in first["changes"]}
    assert {"TIMING_CHANGED", "RECURRENCE_CHANGED", "VISIT_ADDED", "VISIT_RENAMED"} <= kinds
    # a visit the mapping says was re-coded is ONE change, not a removal plus an addition
    assert not any(c["change_type"] == "VISIT_REMOVED" for c in first["changes"])
    assert [c["entity_code"] for c in first["changes"]
            if c["change_type"] == "VISIT_ADDED"] == ["SAFETY_CALL"]
    rows = {(m["mapping_type"], tuple(m["source_codes"]), tuple(m["target_codes"])): m
            for m in first["mappings"]}
    rename = rows[("RENAMED", ("IMAGING_BASELINE",), ("IMAGING_BL",))]
    assert rename["review_status"] == "PROPOSED"                     # a name match is a proposal
    assert rows[("ADDED", (), ("SAFETY_CALL",))]["review_status"] == "AUTO_CONFIRMED"
    assert not first["ready_for_adoption"]
    assert [p["code"] for p in first["problems"]] == ["MAPPING_UNREVIEWED"]


def test_every_material_change_is_traced_to_the_protocol_text_on_both_sides(world):
    view = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    timing = next(c for c in view["changes"]
                  if c["change_type"] == "TIMING_CHANGED" and c["entity_code"] == "C1D1")
    for side in ("from", "to"):
        [source] = timing["sources"][side]
        assert source["page_number"] == 1 and source["section_title"] == "Synthetic pilot schedule"
        assert "Treatment Day 1" in source["source_text"]
    added = next(c for c in view["changes"] if c["change_type"] == "VISIT_ADDED")
    assert added["sources"]["from"] == [] and len(added["sources"]["to"]) == 1   # new in V2 only
    renamed = next(c for c in view["changes"] if c["change_type"] == "VISIT_RENAMED")
    assert renamed["entity_code"] == "IMAGING_BASELINE→IMAGING_BL"
    assert len(renamed["sources"]["from"]) == 1 and len(renamed["sources"]["to"]) == 1


def test_a_pair_needs_declared_lineage_and_the_caller_s_organization(world):
    other_org = uuid4()
    with pytest.raises(RecordNotFoundError):
        world.service.prepare_pair(
            organization_id=other_org, actor_id=world.reviewer,
            from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    with pytest.raises(AmendmentBlockedError) as raised:
        world.service.prepare_pair(
            organization_id=world.org, actor_id=world.reviewer,
            from_schedule_version_id=world.v2, to_schedule_version_id=world.v1)
    assert raised.value.blockers[0]["code"] == "PAIR_NO_LINEAGE"


def review_ready(world, pair):
    rename = next(m for m in pair["mappings"] if m["review_status"] == "PROPOSED")
    return world.service.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=__import__("uuid").UUID(pair["pair_id"]),
        expected_revision=pair["mapping_set_revision"], action="CONFIRM",
        mapping_id=__import__("uuid").UUID(rename["id"]), reason="same imaging visit, re-coded")


def test_review_confirms_with_an_authenticated_decision_and_opens_the_gate(world):
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    reviewed = review_ready(world, pair)
    assert reviewed["ready_for_adoption"] and reviewed["problems"] == []
    assert reviewed["mapping_set_revision"] == pair["mapping_set_revision"] + 1
    assert reviewed["mapping_revision"] != pair["mapping_revision"]
    confirmed = next(m for m in reviewed["mappings"] if m["mapping_type"] == "RENAMED")
    assert confirmed["review_status"] == "CONFIRMED" and confirmed["reviewed_by"] == str(world.reviewer)
    decisions = [r for r in reviewed["reviews"] if r["kind"] == "CONFIRM"]
    assert len(decisions) == 1 and decisions[0]["reviewer_id"] == str(world.reviewer)
    assert decisions[0]["reason"] and decisions[0]["base_revision"] == pair["mapping_set_revision"]


def test_a_stale_revision_is_refused_and_a_reason_is_required(world):
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    review_ready(world, pair)
    from uuid import UUID
    rename = next(m for m in pair["mappings"] if m["review_status"] == "PROPOSED")
    with pytest.raises(StaleRevisionError):
        world.service.review_mapping(
            organization_id=world.org, actor_id=uuid4(), pair_id=UUID(pair["pair_id"]),
            expected_revision=pair["mapping_set_revision"], action="REJECT",
            mapping_id=UUID(rename["id"]), reason="someone else got there first")
    with pytest.raises(AmendmentBlockedError):
        world.service.review_mapping(
            organization_id=world.org, actor_id=uuid4(), pair_id=UUID(pair["pair_id"]),
            expected_revision=pair["mapping_set_revision"] + 1, action="REJECT",
            mapping_id=UUID(rename["id"]), reason=" ")


def test_reject_then_replace_supplies_effective_coverage(world):
    from uuid import UUID
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    rename = next(m for m in pair["mappings"] if m["review_status"] == "PROPOSED")
    pid = UUID(pair["pair_id"])
    rejected = world.service.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=pid,
        expected_revision=pair["mapping_set_revision"], action="REJECT",
        mapping_id=UUID(rename["id"]), reason="a different imaging visit")
    assert {p["code"] for p in rejected["problems"]} == {
        "MAPPING_SOURCE_COVERAGE", "MAPPING_TARGET_COVERAGE"}
    # replacing a still-covered visit is refused; these are now uncovered
    for body in (
            dict(mapping_type="REMOVED", source_codes=["IMAGING_BASELINE"], target_codes=[]),
            dict(mapping_type="ADDED", source_codes=[], target_codes=["IMAGING_BL"])):
        rejected = world.service.review_mapping(
            organization_id=world.org, actor_id=world.reviewer, pair_id=pid,
            expected_revision=rejected["mapping_set_revision"], action="REPLACE",
            reason="removed in the amendment, a new visit was added", **body)
    assert rejected["ready_for_adoption"], rejected["problems"]
    kept = [m for m in rejected["mappings"] if m["review_status"] == "REJECTED"]
    assert len(kept) == 1                                   # the rejected proposal stays on record
    with pytest.raises(AmendmentBlockedError) as raised:
        world.service.review_mapping(
            organization_id=world.org, actor_id=world.reviewer, pair_id=pid,
            expected_revision=rejected["mapping_set_revision"], action="REPLACE",
            mapping_type="RENAMED", source_codes=["C1D1"], target_codes=["C1D1"],
            reason="duplicate coverage")
    assert raised.value.blockers[0]["code"] == "REPLACE_OVERLAPS"


def test_a_tampered_stored_mapping_set_fails_the_gate(world):
    """A row claiming server-derived confirmation that the versions do not support, or a
    reviewed row without a recorded decision, is not trusted."""
    from uuid import UUID
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    pid = UUID(pair["pair_id"])

    def forge(tx):
        record = tx.amendments.get_mapping_set(world.org, pid)
        docs = [dict(item) for item in record.mappings]
        for item in docs:
            if item["review_status"] == "PROPOSED":
                item.update(review_status="CONFIRMED", reviewed_by=str(uuid4()),
                            provenance="REVIEWER")
        tx.amendments.save_mapping_set(
            record.model_copy(update={"mappings": docs}), expected_revision=record.revision)

    world.uow.run(forge)
    view = world.service.pair_view(organization_id=world.org, pair_id=pid)
    codes = {p["code"] for p in view["problems"]}
    assert "MAPPING_REVIEW_NOT_RECORDED" in codes and "MAPPING_REVISION_MISMATCH" in codes
    assert not view["ready_for_adoption"]


def test_pair_view_refuses_when_an_approved_version_no_longer_matches_its_hash(world):
    from uuid import UUID
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    if world.uow.__class__.__name__ == "InMemoryUnitOfWork":
        store = world.uow._store
        row = store.versions[world.v2]
        event_id = next(iter(row["events"]))
        row["events"][event_id]["event"]["display_name"] = "tampered"
    else:
        # a write that bypasses the repository guards, as a damaged or hand-edited store would
        world.uow._database["schedule_events"].update_one(
            {"schedule_version_id": world.v2}, {"$set": {"event.display_name": "tampered"}})
    with pytest.raises(AmendmentIntegrityError):
        world.service.pair_view(organization_id=world.org, pair_id=UUID(pair["pair_id"]))


def test_a_cross_tenant_caller_cannot_see_the_pair(world):
    from uuid import UUID
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    with pytest.raises(RecordNotFoundError):
        world.service.pair_view(organization_id=uuid4(), pair_id=UUID(pair["pair_id"]))
    assert world.service.list_for_trial(
        organization_id=uuid4(), trial_id=world.uow.read(
            lambda tx: tx.schedules.get_header(world.v1)).trial_id) == []


def test_rejecting_a_proposed_rename_shows_the_plain_removal_and_addition_again(world):
    from uuid import UUID
    pair = world.service.prepare_pair(
        organization_id=world.org, actor_id=world.reviewer,
        from_schedule_version_id=world.v1, to_schedule_version_id=world.v2)
    rename = next(m for m in pair["mappings"] if m["review_status"] == "PROPOSED")
    view = world.service.review_mapping(
        organization_id=world.org, actor_id=world.reviewer, pair_id=UUID(pair["pair_id"]),
        expected_revision=pair["mapping_set_revision"], action="REJECT",
        mapping_id=UUID(rename["id"]), reason="a different visit")
    kinds = {(c["change_type"], c["entity_code"]) for c in view["changes"]}
    assert ("VISIT_REMOVED", "IMAGING_BASELINE") in kinds and ("VISIT_ADDED", "IMAGING_BL") in kinds
    assert not any(c["change_type"] == "VISIT_RENAMED" for c in view["changes"])
    removed = next(c for c in view["changes"] if c["change_type"] == "VISIT_REMOVED")
    assert len(removed["sources"]["from"]) == 1 and removed["sources"]["to"] == []
