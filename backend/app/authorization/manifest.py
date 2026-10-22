"""Authorization declarations for every registered UCTSM route (P3-03).

The runtime guard looks a request up here by ``(METHOD, route path)``. A route
with no entry is denied (403 ``ROUTE_NOT_DECLARED``), and
``tests/test_uctsm_authz_manifest.py`` compares this table with the routes the
application actually registers, so a new route cannot ship unguarded.

``resource`` names what the path identifies and ``params`` names which path
parameters carry the identifier(s). ``any_of`` lets one route accept several
capabilities where the exact one depends on the request body; the handler then
calls ``user["_authz"].require(<capability>)`` for the specific one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .catalog import Capability as C, Privacy
from .policy import ResourceKind as K

SQL_PREFIX = "/api/uctsm"
MONGO_PREFIX = "/api/uctsm/mongo-plane"


@dataclass(frozen=True)
class RouteRule:
    capabilities: tuple[C, ...]                 # any_of
    resource: K = K.NONE
    params: tuple[str, ...] = ()
    privacy: Privacy = Privacy.NONE
    #: "sql" (canonical SQL ids) or "mongo_plane" (canonical trial + operational patient)
    plane: str = "sql"
    #: an optional ``trial_id`` query parameter narrows an otherwise organization-
    #: wide list; the guard then also yields the set of authorised trials.
    list_scoped: bool = False
    mutation: str = ""                          # service/handler that mutates, for the manifest
    #: narrow ``capabilities`` by a field of the JSON body before any side effect
    body_field: str | None = None
    body_map: Mapping[str, C] | None = None
    #: when the body's value is NOT in ``body_map`` (or absent) require only the
    #: FIRST capability - the strictest - instead of accepting any of them.
    strict_unmapped: bool = False

    @property
    def capability(self) -> C:
        return self.capabilities[0]


def _r(cap: C | tuple[C, ...], resource: K = K.NONE, *params: str, privacy: Privacy = Privacy.NONE,
       plane: str = "sql", list_scoped: bool = False, mutation: str = "",
       body_field: str | None = None, body_map: Mapping[str, C] | None = None,
       strict_unmapped: bool = False) -> RouteRule:
    caps = cap if isinstance(cap, tuple) else (cap,)
    return RouteRule(caps, resource, tuple(params), privacy, plane, list_scoped, mutation,
                     body_field, body_map, strict_unmapped)


SV = K.SCHEDULE_VERSION
P = K.PATIENT

_SQL: dict[tuple[str, str], RouteRule] = {
    ("POST", "/demo/seed"): _r(C.DEMO_SEED, mutation="DemoService.seed"),
    ("POST", "/trials"): _r(C.SCHEDULE_AUTHOR, mutation="create_trial"),
    ("POST", "/trials/{trial_id}/protocols"): _r(C.SCHEDULE_AUTHOR, K.TRIAL, "trial_id", mutation="create_protocol"),
    ("POST", "/protocols/{protocol_id}/versions"): _r(C.SCHEDULE_AUTHOR, K.PROTOCOL, "protocol_id", mutation="create_protocol_version"),
    ("GET", "/trials/{trial_id}/approved-schedules"): _r(C.SCHEDULE_READ, K.TRIAL, "trial_id", privacy=Privacy.SCHEDULE),
    ("POST", "/trials/{trial_id}/patients"): _r(C.PATIENT_ENROL, K.TRIAL, "trial_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.enrol"),
    ("POST", "/protocols/{protocol_id}/versions/{protocol_version_id}/extract-schedule"): _r(
        C.SCHEDULE_EXTRACT, K.PROTOCOL, "protocol_id", "protocol_version_id", mutation="ExtractionService.queue"),
    ("GET", "/extraction-runs/{run_id}"): _r(C.SCHEDULE_EVIDENCE_READ, K.EXTRACTION_RUN, "run_id", privacy=Privacy.EVIDENCE),
    ("GET", "/schedule-versions/pending-review"): _r(
        (C.SCHEDULE_REVIEW, C.SCHEDULE_APPROVE), privacy=Privacy.SCHEDULE, list_scoped=True),
    ("GET", "/schedule-versions/{schedule_version_id}"): _r(C.SCHEDULE_READ, SV, "schedule_version_id", privacy=Privacy.SCHEDULE),
    ("POST", "/schedule-versions/{schedule_version_id}/validate"): _r(C.SCHEDULE_VALIDATE, SV, "schedule_version_id", mutation="ScheduleValidator"),
    ("POST", "/schedule-versions/{schedule_version_id}/review-decisions"): _r(C.SCHEDULE_REVIEW, SV, "schedule_version_id", mutation="ScheduleReviewService.record_decision"),
    ("POST", "/schedule-versions/{schedule_version_id}/extraction-issues/{issue_id}/resolve"): _r(C.SCHEDULE_REVIEW, SV, "schedule_version_id", mutation="ScheduleReviewService.resolve_extraction_issue"),
    ("POST", "/schedule-versions/{schedule_version_id}/bulk-confirm"): _r(C.SCHEDULE_REVIEW, SV, "schedule_version_id", mutation="ScheduleReviewService.bulk_confirm_all_fields"),
    ("POST", "/schedule-versions/{schedule_version_id}/approve-table"): _r(C.SCHEDULE_APPROVE, SV, "schedule_version_id", mutation="ScheduleReviewService.approve_reviewed_table"),
    ("PUT", "/schedule-versions/{schedule_version_id}/events/{event_id}"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.edit_event"),
    ("PATCH", "/schedule-versions/{schedule_version_id}/events/{event_id}"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.edit_event"),
    ("POST", "/schedule-versions/{schedule_version_id}/qualifiers/{qualifier_id}/resolve"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.resolve_qualifier"),
    ("GET", "/schedule-versions/{schedule_version_id}/qualifiers"): _r(C.SCHEDULE_EVIDENCE_READ, SV, "schedule_version_id", privacy=Privacy.EVIDENCE),
    ("POST", "/schedule-versions/{schedule_version_id}/submit-review"): _r(C.SCHEDULE_REVIEW, SV, "schedule_version_id", mutation="ScheduleReviewService.submit_for_review"),
    ("POST", "/schedule-versions/{schedule_version_id}/review"): _r(
        (C.SCHEDULE_APPROVE, C.SCHEDULE_REJECT), SV, "schedule_version_id",
        mutation="ScheduleReviewService.approve|reject",
        body_field="decision",
        body_map={"APPROVE": C.SCHEDULE_APPROVE, "REJECT": C.SCHEDULE_REJECT}),
    ("GET", "/schedule-versions/{schedule_version_id}/review-view"): _r(C.SCHEDULE_EVIDENCE_READ, SV, "schedule_version_id", privacy=Privacy.EVIDENCE),
    ("POST", "/schedule-versions/{schedule_version_id}/events"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.add_event"),
    ("POST", "/schedule-versions/{schedule_version_id}/tracks"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.set_tracks"),
    ("POST", "/schedule-versions/{schedule_version_id}/holiday-calendars"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.attach_holiday_calendar"),
    ("POST", "/schedule-versions/{schedule_version_id}/review-questions/{question_id}/resolve"): _r(C.SCHEDULE_CORRECT, SV, "schedule_version_id", mutation="ReviewWorkflow.resolve_question"),
    ("POST", "/schedule-versions/{schedule_version_id}/approve-version"): _r(C.SCHEDULE_APPROVE, SV, "schedule_version_id", mutation="ReviewWorkflow.approve"),
    ("GET", "/schedule-versions/{schedule_version_id}/export"): _r(C.SCHEDULE_EXPORT, SV, "schedule_version_id", privacy=Privacy.EVIDENCE),
    ("GET", "/schedule-versions/{schedule_version_id}/accountability"): _r(C.SCHEDULE_EVIDENCE_READ, SV, "schedule_version_id", privacy=Privacy.EVIDENCE),
    ("GET", "/schedule-versions/{schedule_version_id}/projection"): _r(C.SCHEDULE_READ, SV, "schedule_version_id", privacy=Privacy.SCHEDULE),
    ("GET", "/schedule-versions/{schedule_version_id}/enrolment-options"): _r(C.SCHEDULE_READ, SV, "schedule_version_id", privacy=Privacy.SCHEDULE),
    ("GET", "/schedule-versions/{left_id}/diff/{right_id}"): _r(C.SCHEDULE_READ, SV, "left_id", "right_id", privacy=Privacy.SCHEDULE),
    ("POST", "/patients/{patient_id}/schedule/evaluate"): _r(C.PATIENT_SCHEDULE_EVALUATE, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.evaluate"),
    ("GET", "/patients/{patient_id}/schedule"): _r(C.PATIENT_SCHEDULE_READ, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL),
    ("GET", "/patients/{patient_id}/schedule/projection"): _r(C.PATIENT_SCHEDULE_READ, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL),
    ("GET", "/dashboard/actions"): _r(C.DASHBOARD_READ, privacy=Privacy.PATIENT_DEIDENTIFIED, list_scoped=True),
    ("GET", "/patients/{patient_id}/deviations"): _r(C.PATIENT_SCHEDULE_READ, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL),
    ("POST", "/patients/{patient_id}/occurrence-facts"): _r(C.PATIENT_OCCURRENCE_RECORD, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.record_occurrence_fact"),
    ("POST", "/patients/{patient_id}/activities"): _r(C.PATIENT_OCCURRENCE_RECORD, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.record_activity"),
    ("POST", "/patients/{patient_id}/parity-check"): _r(C.PARITY_RUN, P, "patient_id", mutation="ParityService.compare_patient"),
    ("GET", "/trials/{trial_id}/read-mode"): _r(C.TRIAL_READ_MODE_READ, K.TRIAL, "trial_id"),
    ("POST", "/trials/{trial_id}/read-mode"): _r(C.TRIAL_READ_MODE_SET, K.TRIAL, "trial_id", mutation="ParityService.set_read_mode"),
    ("POST", "/trials/{trial_id}/parity-runs/{run_id}/accept"): _r(C.PARITY_ACCEPT, K.PARITY_RUN, "trial_id", "run_id", mutation="ParityService.accept_run"),
    ("GET", "/patients/{patient_id}/anchors"): _r(C.PATIENT_SCHEDULE_READ, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL),
    ("POST", "/patients/{patient_id}/unscheduled-visits"): _r(C.PATIENT_UNSCHEDULED_CREATE, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.create_unscheduled_visit"),
    ("GET", "/patients/{patient_id}/conditions"): _r(C.PATIENT_SCHEDULE_READ, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL),
    ("POST", "/patients/{patient_id}/conditions/impact-preview"): _r(C.PATIENT_IMPACT_PREVIEW, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.preview"),
    ("POST", "/patients/{patient_id}/assignment/impact-preview"): _r(C.PATIENT_IMPACT_PREVIEW, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.preview"),
    ("POST", "/patients/{patient_id}/schedule/impact-preview"): _r(C.PATIENT_IMPACT_PREVIEW, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.preview"),
    ("POST", "/schedule-impact-proposals/{proposal_id}/confirm"): _r(C.PATIENT_IMPACT_CONFIRM, K.IMPACT_PROPOSAL, "proposal_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.confirm"),
    ("POST", "/schedule-impact-proposals/{proposal_id}/cancel"): _r(C.PATIENT_IMPACT_CONFIRM, K.IMPACT_PROPOSAL, "proposal_id", mutation="PatientScheduleImpactService.cancel"),
    ("GET", "/patient-events/{patient_event_id}"): _r(C.PATIENT_SCHEDULE_READ, K.PATIENT_EVENT, "patient_event_id", privacy=Privacy.PATIENT_CLINICAL),
    ("POST", "/patient-events/{patient_event_id}/occurrences"): _r(C.PATIENT_OCCURRENCE_RECORD, K.PATIENT_EVENT, "patient_event_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.record_occurrence"),
    ("POST", "/patients/{patient_id}/anchors"): _r(C.PATIENT_ANCHOR_RECORD, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="AnchorService.record"),
    ("POST", "/patients/{patient_id}/anchors/{candidate_id}/impact-preview"): _r(C.PATIENT_IMPACT_PREVIEW, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.preview"),
    ("POST", "/patients/{patient_id}/states"): _r(C.PATIENT_STATE_RECORD, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleService.record_state"),
    ("POST", "/patients/{patient_id}/states/impact-preview"): _r(C.PATIENT_IMPACT_PREVIEW, P, "patient_id", privacy=Privacy.PATIENT_CLINICAL, mutation="PatientScheduleImpactService.preview"),
}

_MONGO: dict[tuple[str, str], RouteRule] = {
    ("GET", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/review-view"): _r(
        C.SCHEDULE_EVIDENCE_READ, privacy=Privacy.EVIDENCE, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/approve-table"): _r(
        C.SCHEDULE_APPROVE, plane="mongo_plane", mutation="app.application.schedule_review.ScheduleReviewService.approve_reviewed_table"),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/actual-occurrences"): _r(
        C.PATIENT_OCCURRENCE_RECORD, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.patient_schedule.PatientScheduleService.record_actual_occurrence"),
    ("GET", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/conflicts"): _r(
        C.SCHEDULE_EVIDENCE_READ, privacy=Privacy.EVIDENCE, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/conflicts/{event_id}/resolve"): _r(
        C.SCHEDULE_CORRECT, plane="mongo_plane",
        mutation="app.application.conflict_resolution.ConflictResolutionService.resolve_event"),
    ("GET", "/trials/{trial_id}/patients/{external_patient_id}/calendar"): _r(
        C.PATIENT_SCHEDULE_READ, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane"),
    # P5: typed commands. The capability is narrowed by the body's ``type`` before
    # any side effect; an unknown / high-impact type needs the strictest one.
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/commands/preview"): _r(
        (C.PATIENT_COMMAND_HIGH_IMPACT, C.PATIENT_OCCURRENCE_RECORD, C.PATIENT_UNSCHEDULED_CREATE),
        P, "trial_id", "external_patient_id", privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.patient_commands.PatientCommandService.preview",
        body_field="type", body_map={
            "RECORD_ACTUAL": C.PATIENT_OCCURRENCE_RECORD, "MARK_MISSED": C.PATIENT_OCCURRENCE_RECORD,
            "RESCHEDULE_APPOINTMENT": C.PATIENT_OCCURRENCE_RECORD,
            "CREATE_UNSCHEDULED": C.PATIENT_UNSCHEDULED_CREATE}, strict_unmapped=True),
    # the proposal names its own command type: the handler re-checks the exact
    # capability that type needs (any_of here only admits the three holders)
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/commands/{proposal_id}/confirm"): _r(
        (C.PATIENT_COMMAND_HIGH_IMPACT, C.PATIENT_OCCURRENCE_RECORD, C.PATIENT_UNSCHEDULED_CREATE),
        P, "trial_id", "external_patient_id", privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.patient_commands.PatientCommandService.confirm"),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/commands/{proposal_id}/cancel"): _r(
        (C.PATIENT_COMMAND_HIGH_IMPACT, C.PATIENT_OCCURRENCE_RECORD, C.PATIENT_UNSCHEDULED_CREATE),
        P, "trial_id", "external_patient_id", privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.patient_commands.PatientCommandService.cancel"),
    # P5 pilot (synthetic, local): seed and a paged, relationship-filtered patient list
    ("POST", "/pilot/seed"): _r(
        C.DEMO_SEED, privacy=Privacy.NONE, plane="mongo_plane",
        mutation="app.application.pilot.PilotService.seed"),
    ("POST", "/pilot/seed-amendment"): _r(
        C.DEMO_SEED, privacy=Privacy.NONE, plane="mongo_plane",
        mutation="app.application.pilot.PilotService.seed_amendment"),
    ("GET", "/trials/{trial_id}/patients"): _r(
        C.PATIENT_SCHEDULE_READ, K.TRIAL, "trial_id", privacy=Privacy.PATIENT_CLINICAL,
        plane="mongo_plane"),
    ("GET", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/required-inputs"): _r(
        C.SCHEDULE_EVIDENCE_READ, privacy=Privacy.EVIDENCE, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/required-inputs/custom"): _r(
        C.SCHEDULE_CORRECT, plane="mongo_plane", mutation="app.application.required_inputs.RequiredInputsService.add_custom"),
    ("POST", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/required-inputs/custom/{code}/remove"): _r(
        C.SCHEDULE_CORRECT, plane="mongo_plane", mutation="app.application.required_inputs.RequiredInputsService.remove_custom"),
    ("POST", "/trials/{trial_id}/schedule-versions/{schedule_version_id}/required-inputs/acknowledge"): _r(
        C.SCHEDULE_REVIEW, plane="mongo_plane", mutation="app.application.required_inputs.RequiredInputsService.acknowledge"),
    ("GET", "/trials/{trial_id}/enrolment-inputs"): _r(
        C.PATIENT_ENROL, K.TRIAL, "trial_id", plane="mongo_plane"),
    ("GET", "/operations/dead-letters"): _r(C.OUTBOX_REPLAY, plane="mongo_plane"),
    ("POST", "/operations/dead-letters/{outbox_id}/replay"): _r(
        C.OUTBOX_REPLAY, plane="mongo_plane", mutation="app.application.outbox_delivery.DeadLetterService.replay"),
    ("POST", "/trials/{trial_id}/cohorts"): _r(
        C.COHORT_CUTOVER, K.TRIAL, "trial_id", plane="mongo_plane", mutation="app.application.cohort_cutover.CohortCutoverService.create_shadow"),
    ("GET", "/trials/{trial_id}/cohorts/{cohort_id}/readiness"): _r(
        C.COHORT_CUTOVER, K.TRIAL, "trial_id", plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/cohorts/{cohort_id}/promote"): _r(
        C.COHORT_CUTOVER, K.TRIAL, "trial_id", plane="mongo_plane", mutation="app.application.cohort_cutover.CohortCutoverService.promote"),
    ("POST", "/trials/{trial_id}/cohorts/{cohort_id}/rollback"): _r(
        C.COHORT_CUTOVER, K.TRIAL, "trial_id", plane="mongo_plane", mutation="app.application.cohort_cutover.CohortCutoverService.rollback"),
    ("GET", "/trials/{trial_id}/patients/{external_patient_id}/notification-preferences"): _r(
        C.PATIENT_SCHEDULE_READ, P, "trial_id", "external_patient_id", privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane"),
    ("PUT", "/trials/{trial_id}/patients/{external_patient_id}/notification-preferences"): _r(
        C.PATIENT_STATE_RECORD, P, "trial_id", "external_patient_id", privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.reminder_sync.ReminderSyncService.record_preferences"),
    ("GET", "/trials/{trial_id}/reports/schedule"): _r(
        C.PATIENT_SCHEDULE_READ, K.TRIAL, "trial_id", privacy=Privacy.PATIENT_CLINICAL,
        plane="mongo_plane"),
    ("GET", "/trials/{trial_id}/patients/{external_patient_id}/commands"): _r(
        C.PATIENT_COMMAND_PREVIEW, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane"),
    # P6: protocol amendments. Trial-level routes carry a capability only here; the handler
    # then checks the caller's relationship to the persisted trial. Adoption needs
    # ``amendment.adopt`` (PI-only, never a role default or a self-grant); a return also
    # needs ``patient.command.high_impact`` (checked in the handler, on every call).
    ("GET", "/trials/{trial_id}/amendments"): _r(
        C.SCHEDULE_READ, privacy=Privacy.SCHEDULE, plane="mongo_plane"),
    ("GET", "/trials/{trial_id}/amendments/rule-handling"): _r(
        C.SCHEDULE_EVIDENCE_READ, privacy=Privacy.EVIDENCE, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/amendments/rule-handling"): _r(
        C.SCHEDULE_CORRECT, plane="mongo_plane", mutation="app.application.manual_rules.ManualRuleService.decide"),
    ("POST", "/trials/{trial_id}/amendments/rule-handling/approve"): _r(
        C.SCHEDULE_APPROVE, plane="mongo_plane", mutation="app.application.manual_rules.ManualRuleService.decide"),
    ("POST", "/trials/{trial_id}/amendments/lineage"): _r(
        C.SCHEDULE_AUTHOR, plane="mongo_plane",
        mutation="app.application.amendment_service.AmendmentService.declare_lineage"),
    ("POST", "/trials/{trial_id}/amendments/pairs"): _r(
        C.SCHEDULE_REVIEW, plane="mongo_plane",
        mutation="app.application.amendment_service.AmendmentService.prepare_pair"),
    ("GET", "/trials/{trial_id}/amendments/pairs/{pair_id}"): _r(
        C.SCHEDULE_READ, privacy=Privacy.SCHEDULE, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/amendments/pairs/{pair_id}/mappings"): _r(
        C.SCHEDULE_REVIEW, plane="mongo_plane",
        mutation="app.application.amendment_service.AmendmentService.review_mapping"),
    ("GET", "/trials/{trial_id}/patients/{external_patient_id}/amendments"): _r(
        C.PATIENT_IMPACT_PREVIEW, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane"),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/amendments/simulate"): _r(
        C.PATIENT_IMPACT_PREVIEW, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.amendment_adoption.AdoptionService.simulate"),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/amendments/simulations/{simulation_id}/confirm"): _r(
        C.AMENDMENT_ADOPT, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.amendment_adoption.AdoptionService.confirm",
        body_field="decision", body_map={
            "ADOPT": C.AMENDMENT_ADOPT, "REMAIN": C.AMENDMENT_ADOPT, "RETURN": C.AMENDMENT_ADOPT},
        strict_unmapped=True),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/amendments/decisions/{decision_id}/execute"): _r(
        C.AMENDMENT_ADOPT, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.amendment_adoption.AdoptionService.execute"),
    ("POST", "/trials/{trial_id}/patients/{external_patient_id}/amendments/decisions/{decision_id}/cancel"): _r(
        C.AMENDMENT_ADOPT, P, "trial_id", "external_patient_id",
        privacy=Privacy.PATIENT_CLINICAL, plane="mongo_plane",
        mutation="app.application.amendment_adoption.AdoptionService.cancel"),
}


def _expand(prefix: str, table: Mapping[tuple[str, str], RouteRule]) -> dict[tuple[str, str], RouteRule]:
    return {(method, prefix + path): rule for (method, path), rule in table.items()}


MANIFEST: dict[tuple[str, str], RouteRule] = {
    **_expand(SQL_PREFIX, _SQL), **_expand(MONGO_PREFIX, _MONGO),
}


def rule_for(method: str, route_path: str) -> RouteRule | None:
    return MANIFEST.get((method.upper(), route_path))
