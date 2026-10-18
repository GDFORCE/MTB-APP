export type InterpretationStatus =
  | "EXTRACTED" | "INFERRED" | "CONFIRMED" | "AMBIGUOUS"
  | "CONFLICTING" | "UNRESOLVED" | "REJECTED";

export type ScheduleStatus =
  | "DRAFT" | "EXTRACTED" | "VALIDATION_REQUIRED" | "IN_REVIEW"
  | "APPROVED" | "REJECTED" | "SUPERSEDED" | "ARCHIVED";

export type Evidence = {
  id: string;
  evidence_type: string;
  page_number?: number;
  section_title?: string;
  table_title?: string;
  row_identifier?: string;
  column_identifier?: string;
  source_text?: string;
  source_locator: Record<string, unknown>;
};

export type ValidationIssue = {
  id: string;
  entity_type?: string;
  entity_id?: string;
  issue_code: string;
  severity: "INFO" | "WARNING" | "ERROR" | "CRITICAL";
  message: string;
  blocking: boolean;
  status: "OPEN" | "RESOLVED" | "ACCEPTED" | "DISMISSED";
  details: Record<string, unknown>;
};

export type Qualifier = {
  id: string;
  marker?: string;
  text: string;
  scope: string;
  category: string;
  target_codes: string[];
  resolved: boolean;
  evidence_refs: string[];
};

export type ScheduleActivity = {
  id: string;
  code?: string;
  display_name: string;
  activity_type: string;
  requiredness: string;
  sequence_number?: number;
  timing?: Record<string, unknown> & { type?: string };
  applicability?: unknown[];
  qualifiers?: Qualifier[];
};

/**
 * Doc I8: whose situation a protocol rule is about. Only PATIENT, ARM and
 * COHORT rules may change a patient's visits; SITE/COUNTRY/TRIAL rules belong
 * to a trial-level workflow and the patient engine refuses to act on them.
 */
export type RuleScope = "PATIENT" | "ARM" | "COHORT" | "SITE" | "COUNTRY" | "TRIAL";

/** Doc C6: whether a timing reference may count from a planned anchor value. */
export type AnchorAcceptance = "ACTUAL" | "PLANNED_OR_ACTUAL";

/**
 * Doc B2/B3: a visit's allowed window, independent of its timing type and
 * applied to every occurrence. May be one-sided (-0/+3). `null`/absent means
 * the protocol states no window - it is never defaulted (doc B4).
 */
export type EventWindow = {
  before: { value: number; unit: string };
  after: { value: number; unit: string };
};

/** Doc F1-F3: how each treatment cycle's start is dated. */
export type CycleDependencyMode =
  | "NOMINAL" | "ACTUAL_PREVIOUS_CYCLE_START" | "MANUAL" | "UNCLEAR";

/** RecurrenceTermination as the backend serialises it (`date` is the alias). */
export type RecurrenceTermination = {
  type: "COUNT" | "DATE" | "EVENT" | "CONDITION" | "HORIZON" | "RELATIVE";
  count?: number | null;
  date?: string | null;
  event_code?: string | null;
  end_reference?: { code?: string } | null;
  end_offset?: { value: number; unit: string } | null;
  inclusive?: boolean | null;
};

/** Doc F1-F5: treatment cycles as addressable starts (<code>_<n>_START anchors). */
export type CyclePlan = {
  code: string;
  start_reference: { kind?: "ANCHOR"; code: string; accepts?: AnchorAcceptance };
  length: { value: number; unit: string };
  dependency_mode: CycleDependencyMode;
  /** The Day-1 visit whose actual dates give each cycle's actual start. */
  start_event_code?: string | null;
  termination: RecurrenceTermination;
};

export type RepeatBlock = {
  id: string;
  code: string;
  protocol_label: string;
  display_name: string;
  event_codes: string[];
  dependency_mode: "NOMINAL" | "ACTUAL_PREVIOUS_EVENT" | "MANUAL" | "UNCLEAR";
  resume_mode?: string;
  cycle_plan?: CyclePlan | null;
};

/**
 * `explanation.cycle` on a Cycle-N-Day-D patient visit. `start_basis` says
 * where the cycle start came from: NOMINAL (grid), ACTUAL (recorded),
 * FROM_ACTUAL_PREVIOUS_CYCLE (previous actual start + length), PLANNED
 * (cycle 1 from its anchor, not yet actually given), MANUAL (site-entered),
 * or - undated - WAITING_FOR_ACTUAL_PREVIOUS_CYCLE, WAITING_FOR_MANUAL_DATE,
 * UNCLEAR.
 */
export type CycleExplanation = {
  plan: string;
  number: number;
  day: number;
  anchor: string;
  start: string | null;
  start_basis: string;
  dependency_mode: CycleDependencyMode;
};

/** `explanation.recurrence` on a repeat occurrence; segment keys only on a phased plan. */
export type RecurrenceExplanation = {
  index: number;
  /** 1-based phase of a phased follow-up plan (doc E9). */
  segment?: number;
  /** 0-based position of this visit within its phase. */
  segment_occurrence?: number;
  /** Backend cadence phrase for the phase, e.g. "every 6 months" or "yearly". */
  cadence?: string;
};

/**
 * `dependency_result` on a patient visit. Returned by the per-event endpoint
 * (GET /patient-events/{id}); the schedule list payload does not include it.
 */
export type DependencyResult = {
  mode?: string;
  cycle_plan?: string;
  /** Codes (visit codes, or <plan>_<n>_START cycle anchors) still needing an actual date. */
  waiting_for_actual?: string[];
  /** Codes whose date a person must enter (MANUAL mode). */
  waiting_for_manual_date?: string[];
  blocked_by?: unknown;
};

/** One conditional rule as it sits in the protocol schedule definition. */
export type ProtocolConditionalDefinition = {
  id: string;
  code: string;
  protocol_label: string;
  display_name: string;
  /** Absent on schedules stored before scopes existed; those are PATIENT. */
  scope?: RuleScope;
  actions: Array<{ action_type: string; target_code: string }>;
  requires_review: boolean;
  interpretation_status: InterpretationStatus;
  evidence_refs: string[];
};

export type ConditionalDefinition = {
  condition_code: string;
  /** Not sent by the patient-conditions endpoint today; looked up from the
   * schedule's own conditional_definitions when absent. */
  scope?: RuleScope;
  display_name: string;
  protocol_label: string;
  state: "NOT_OCCURRED" | "PENDING_CONFIRMATION" | "ACTIVE" | "RESOLVED"
    | "NOT_APPLICABLE" | "CANCELLED";
  occurrence_date?: string;
  resolution_date?: string;
  requires_review: boolean;
  interpretation_status: InterpretationStatus;
  actions: Array<{ action_type: string; target_code: string }>;
  evidence_refs: string[];
};

export type PatientConditionsResponse = {
  patient_id: string;
  schedule_version_id?: string;
  conditions: ConditionalDefinition[];
};

export type PatientActivity = {
  id: string;
  activity_definition_id: string;
  activity_code?: string;
  status: string;
  requiredness: string;
  sequence_number?: number;
  planned_time?: string;
  earliest_time?: string;
  latest_time?: string;
  actual_time?: string;
  explanation?: Record<string, unknown>;
};

export type ScheduleEvent = {
  id: string;
  code: string;
  protocol_label: string;
  display_name: string;
  event_type: string;
  timing: Record<string, unknown> & { type: string };
  applicability: unknown[];
  conditions: unknown[];
  dependencies: unknown[];
  dependency_mode?: "NOMINAL" | "ACTUAL_PREVIOUS_EVENT" | "MANUAL" | "UNCLEAR";
  /** Doc 10 s2-s3. Null when the protocol never stated a mode. */
  visit_mode?: string | null;
  /** Doc 10 s31-s32. Every mode a hybrid visit permits. */
  allowed_visit_modes?: string[];
  /** Doc 10 s13-s15. ON_DEMAND visits exist but are never due. */
  activation?: "SCHEDULED" | "ON_DEMAND";
  /** Doc B2/B3. Absent or null: the protocol states no window. */
  window?: EventWindow | null;
  recurrence?: Record<string, unknown>;
  /** Doc E9: a phased follow-up plan (SEGMENTED), mutually exclusive with `recurrence`. */
  recurrence_plan?: Record<string, unknown> | null;
  conditional_actions?: unknown[];
  qualifiers?: Qualifier[];
  confinement?: Record<string, unknown>;
  activities: ScheduleActivity[];
  evidence_refs: string[];
  interpretation_status: InterpretationStatus;
  requires_review: boolean;
};

export type UniversalSchedule = {
  schema_version: "uctsm.v1";
  schedule_version_id: string;
  schedule_metadata: {
    name: string;
    description?: string;
    schedule_type: string;
    version_number: number;
    status: ScheduleStatus;
    day_numbering?: { day_1_anchor: { code: string } } | null;
  };
  anchors: Array<{ id: string; code: string; display_name: string; status: string;
    source_condition_code?: string | null; derivation_rule?: Record<string, unknown> | null }>;
  events: ScheduleEvent[];
  conditional_definitions?: ProtocolConditionalDefinition[];
  repeat_blocks?: RepeatBlock[];
  /** WP3: independently scoped processes (interventions, follow-up). */
  processes?: { code: string; display_name: string; kind: string; event_codes: string[];
    hold_resume_policy?: string; resume_trigger?: string | null }[];
  arms?: { code: string; display_name: string }[];
  cohorts?: { code: string; display_name: string }[];
  populations?: { code: string; display_name: string }[];
  evidence: Evidence[];
  validation_issues: ValidationIssue[];
};

export type ScheduleProjection = {
  event_id: string;
  event_code: string;
  title: string;
  timing_display: string;
  window_display?: string;
  event_type_display: string;
  activities_display: string[];
  condition_display?: string;
  status: InterpretationStatus;
  requires_review: boolean;
  evidence_refs: string[];
};

export type ApprovedScheduleSummary = {
  schedule_definition_id: string;
  schedule_version_id: string;
  name: string;
  schedule_type: string;
  version_number: number;
  approved_at?: string;
};

/** Every patient visit status the evaluator produces (doc Part 3). */
export type PatientEventStatus =
  | "RESOLVED" | "AVAILABLE_ON_DEMAND" | "WAITING_FOR_ANCHOR"
  | "WAITING_FOR_CONDITION" | "WAITING_FOR_ASSIGNMENT" | "NOT_APPLICABLE"
  | "BLOCKED" | "PAUSED" | "UNRESOLVED" | "CANCELLED" | "COMPLETED" | "MISSED";

export type PatientScheduleEvent = {
  id: string;
  event_definition_id: string;
  occurrence_index: number;
  status: PatientEventStatus | string;
  nominal_start_date?: string;
  nominal_end_date?: string;
  earliest_date?: string;
  latest_date?: string;
  actual_date?: string;
  timing_resolution?: Record<string, unknown>;
  activities?: PatientActivity[];
  /**
   * Evaluator explanation. Keys the UI reads:
   * - `waiting_for_assignment: string[]` - dimension codes (ARM, COHORT...) the
   *   patient has not been assigned yet (status WAITING_FOR_ASSIGNMENT, doc H2);
   * - `anchor_basis: Record<code, "ACTUAL" | "PLANNED">` - which anchor values
   *   dated this visit; PLANNED means the date is provisional (doc C6/D2);
   * - `waiting_for_actual_anchor: string[]` - anchors that have only a planned
   *   value while this visit's rule needs the actual one (doc D2);
   * - `cycle: CycleExplanation` - on a Cycle-N-Day-D visit (doc F1-F5);
   * - `recurrence: RecurrenceExplanation` - on a repeat occurrence (doc E9).
   */
  explanation?: Record<string, unknown>;
  /** Present on the per-event endpoint; the list payload does not carry it. */
  dependency_result?: DependencyResult;
  /** Doc 10 s31-s32. Absent when the protocol never stated one. */
  visit_mode?: string | null;
  /** Doc 10 s14. Present only on a visit a site created on demand. */
  unscheduled_reason?: string | null;
  /** WP2 constraint-set feasibility; null/absent for legacy rules. */
  feasibility?: "FEASIBLE" | "INCOMPLETE" | "INFEASIBLE" | null;
  feasible_intervals?: FeasibleInterval[];
  missing_inputs?: MissingInput[];
  conflicting_constraints?: string[];
  /** WP3 (read from the persisted evaluator explanation). */
  logical_key?: string;
  process_state?: { process?: string; state?: string; effective_at?: string; reason?: string; shift_days?: number } | null;
  booked_time?: string | null;
  booked_outside_permitted?: boolean | null;
  actual_time?: string | null;
  condition_episode?: { condition?: string; episode?: number } | null;
  held_after_resolution?: string | null;
  calendar_session?: { session?: number; kind?: string; replaces_session?: number | null; slot_date?: string | null } | null;
  /** Newest first. A correction supersedes the newest record of the same occurrence. */
  occurrence_records?: { id: string; kind: string; status: string; actual_date?: string | null;
    booked_date?: string | null; supersedes_occurrence_id?: string | null; recorded_at?: string }[];
};

export type FeasibleBound = {
  /** null = unbounded on this side. */
  value?: string | null;
  inclusive: boolean;
  sources?: string[];
  /** A missing input could still move this side. */
  provisional?: boolean;
};

export type FeasibleInterval = { lower: FeasibleBound; upper: FeasibleBound };

export type MissingInput = {
  kind: string;
  code?: string | null;
  occurrence?: string | null;
  accepts?: string | null;
  reason: string;
  constraint_ids?: string[];
};

/**
 * The per-patient status of one anchor (requirement doc 1 s22). PLANNED and
 * ACTUAL are deliberately separate: an expected date is not evidence the event
 * happened.
 */
export type AnchorStatusView = {
  anchor_code: string;
  display_name: string;
  status:
    | "NOT_REQUIRED" | "AWAITING_EVENT" | "PLANNED"
    | "ACTUAL" | "CONFIRMED" | "CORRECTED";
  value?: string | null;
  reason: string;
  awaiting_event_code?: string | null;
  confirmed: boolean;
};

/**
 * One selectable enrolment dimension (requirement doc 8 s15-s18). ``blocked``
 * means a parent choice is still outstanding, so nothing here can be picked yet.
 */
export type EnrolmentDimension = {
  dimension_type: string;
  display_name: string;
  options: {
    code: string;
    display_name: string;
    description?: string | null;
    parent_dimension_type?: string | null;
    parent_code?: string | null;
  }[];
  depends_on?: string | null;
  blocked: boolean;
  reason?: string | null;
};

/**
 * One classified difference between schedule versions (requirement doc 9 s10).
 * CLINICAL means it can affect a patient already on the study.
 */
export type TypedScheduleChange = {
  change_type: string;
  significance: "CLINICAL" | "ADMINISTRATIVE";
  entity_type: string;
  entity_code: string;
  summary: string;
  detail?: string | null;
};

export type ScheduleDiffResponse = {
  added_events: string[];
  removed_events: string[];
  typed_changes?: TypedScheduleChange[];
  clinically_significant_count?: number;
};

/**
 * A repeat that continues past the occurrences that were materialized
 * (requirement doc 3 s4 and s36). Without this the last row on screen reads as
 * the end of the protocol, which it is not.
 */
export type RepeatRuleSummary = {
  event_code: string;
  block_code?: string | null;
  cadence: string;
  termination: string;
  materialized_count: number;
  continues: boolean;
  next_unmaterialized?: string | null;
  /** Doc E9: one phrase per cadence of a phased plan, in order. */
  segments?: string[];
};

export type PatientScheduleResponse = {
  patient_id: string;
  /** Canonical trial and operational patient id: how this patient is found in the
   * MongoDB patient plane (P5 commands). */
  trial_id?: string;
  external_patient_id?: string | null;
  status: string;
  schedule_version_id?: string;
  evaluation_id?: string;
  events: PatientScheduleEvent[];
  repeat_rules?: RepeatRuleSummary[];
};
