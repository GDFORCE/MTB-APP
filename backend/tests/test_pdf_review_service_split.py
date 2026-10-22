"""P12-02: the SQL review service is split into focused modules behind an unchanged compatibility facade."""
import ast
import inspect
from pathlib import Path

import app.services.schedule_service as facade
from app.services import review_approval, review_base, review_editing, review_queue

SERVICES = Path(__file__).resolve().parents[1] / "app" / "services"

#: The public contract every caller (legacy router, server.py, services, tests) relies on.
REVIEW_SURFACE = {
    "validate": "(self, schedule_version_id: 'UUID') -> 'list[ValidationIssue]'",
    "correct_event": "(self, schedule_version_id: 'UUID', event: 'Event', *, reviewer_id: 'UUID', reason: 'str') -> 'list[ValidationIssue]'",
    "bulk_confirm_all_fields": "(self, schedule_version_id: 'UUID', *, reviewer_id: 'UUID', reason: 'str') -> 'int'",
    "record_decision": None, "resolve_qualifier": None, "resolve_extraction_issue": None,
    "submit_for_review": "(self, schedule_version_id: 'UUID', *, actor_id: 'UUID') -> 'None'",
    "approve_reviewed_table": None, "approve": None,
    "reject": "(self, schedule_version_id: 'UUID', *, reviewer_id: 'UUID', reason: 'str') -> 'None'",
    "list_pending_review": None, "mark_material_edit": None, "enforce_maker_checker": None,
}
RE_EXPORTED = [
    "ASSIGNMENT_FACT_STATE_CODE", "PROCESS_TRANSITION_STATE_CODE", "SITE_HOLIDAY_CALENDAR_STATE_CODE", "RESERVED_STATE_CODES",
    "FACT_KIND_KEY", "SUPERSEDES_KEY", "EFFECTIVE_AT_KEY", "BOOKED_KEY", "END_KEY", "occurrence_fact_from_row", "utc_now",
    "current_protocol_approved_versions", "in_force_today", "resolve_current_schedule_version",
    "_enrollable_protocol_version", "ScheduleReviewService", "PatientScheduleService",
]


def test_every_name_callers_import_from_schedule_service_is_still_there():
    for name in RE_EXPORTED:
        assert hasattr(facade, name), name


def test_the_facade_keeps_the_public_review_methods_and_signatures():
    for name, signature in REVIEW_SURFACE.items():
        method = getattr(facade.ScheduleReviewService, name)
        assert callable(method), name
        if signature:
            assert str(inspect.signature(method)) == signature, name
    assert list(inspect.signature(facade.ScheduleReviewService.__init__).parameters) == ["self", "session", "maker_checker"]


def test_each_responsibility_lives_in_exactly_one_focused_module():
    owners = {
        "ReviewEditing": (review_editing, {"validate", "correct_event", "bulk_confirm_all_fields", "resolve_qualifier",
                                           "record_decision", "resolve_extraction_issue"}),
        "ReviewApproval": (review_approval, {"submit_for_review", "approve_reviewed_table", "approve", "reject"}),
        "ReviewQueue": (review_queue, {"list_pending_review"}),
        "ReviewBase": (review_base, {"mark_material_edit", "enforce_maker_checker", "_provenance", "_audit", "_organization_id"}),
    }
    for class_name, (module, methods) in owners.items():
        cls = getattr(module, class_name)
        assert methods <= set(vars(cls)), class_name                # defined here ...
        for other_name, (_, other_methods) in owners.items():
            if other_name != class_name:
                assert not (methods & other_methods)               # ... and nowhere else
    assert "approve" not in vars(facade.ScheduleReviewService)      # the facade holds no logic of its own
    assert not [n for n in vars(facade.ScheduleReviewService) if not n.startswith("__")]


def test_only_the_approval_module_changes_status_to_approved_and_only_it_touches_the_protocol_pointer():
    for path in SERVICES.glob("review_*.py"):
        source = path.read_text(encoding="utf-8")
        owns = "ScheduleStatus.APPROVED.value" in source and "current_version_id =" in source
        assert owns == (path.name == "review_approval.py"), path.name


def test_no_new_module_exceeds_the_size_that_made_the_old_service_unreadable():
    for name in ("review_base", "review_editing", "review_approval", "review_queue", "schedule_facts",
                 "schedule_version_resolution"):
        assert len((SERVICES / f"{name}.py").read_text(encoding="utf-8").splitlines()) < 350, name
    assert len((SERVICES / "schedule_service.py").read_text(encoding="utf-8").splitlines()) < 900


def test_the_review_modules_import_no_framework_or_http_layer():
    for path in SERVICES.glob("review_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not {m for m in imported if m.startswith(("fastapi", "starlette", "server"))}, path.name


PATIENT_OWNERS = {
    "patient_schedule_reads": ("PatientScheduleReads", {"current_events", "protected_event_ids", "patient_conditions", "evaluate_result"}),
    "patient_schedule_context": ("PatientScheduleContext", {"_context"}),
    "patient_schedule_persistence": ("PatientSchedulePersistence", {"_persist_activities", "_persist_confinements"}),
    "patient_schedule_recording": ("PatientScheduleRecording", {"create_unscheduled_visit", "record_activity",
                                                                "record_occurrence_fact", "record_patient_fact"}),
    "patient_schedule_evaluation": ("PatientScheduleEvaluation", {"evaluate"}),
}


def test_the_patient_service_facade_keeps_every_method_and_holds_no_logic():
    import importlib
    service = facade.PatientScheduleService
    for module_name, (class_name, methods) in PATIENT_OWNERS.items():
        cls = getattr(importlib.import_module(f"app.services.{module_name}"), class_name)
        assert methods <= set(vars(cls)), class_name
        assert all(callable(getattr(service, name)) for name in methods)
    assert not [n for n in vars(service) if not n.startswith("__")]
    assert list(inspect.signature(service.__init__).parameters) == ["self", "session"]
    assert list(inspect.signature(service.evaluate).parameters)[:4] == ["self", "patient_id", "organization_id", "horizon"]


def test_only_the_persistence_and_recording_modules_write_patient_rows_and_reads_never_do():
    for module_name, writes in (("patient_schedule_reads", False), ("patient_schedule_context", False),
                                ("patient_schedule_persistence", True), ("patient_schedule_recording", True)):
        source = (SERVICES / f"{module_name}.py").read_text(encoding="utf-8")
        assert ("session.add(" in source) == writes, module_name
    for name in PATIENT_OWNERS:
        assert len((SERVICES / f"{name}.py").read_text(encoding="utf-8").splitlines()) < 350, name
    assert len((SERVICES / "schedule_service.py").read_text(encoding="utf-8").splitlines()) < 100
