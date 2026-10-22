# UCTSM MongoDB collections

Generated from `app/persistence/mongodb/catalog.py` (P14-01); do not edit by hand.

Identity: every collection except the migration ledger uses an application-generated UUID (binary subtype 4) as `_id`; references are UUID fields, never ObjectIds.

| Collection | Owner | Purpose | References | Bounded-document strategy |
|---|---|---|---|---|
| `_schema_migrations` | platform | Migration ledger (infrastructure; the only collection with ObjectId _id) | - | one document per migration |
| `amendment_decisions` | amendment | Explicit patient adoption decisions | amendment_pairs, patients | one document per decision |
| `amendment_mapping_decisions` | amendment | Append-only mapping review decisions | amendment_mapping_sets | one document per decision |
| `amendment_mapping_sets` | amendment | Reviewed event mapping of a pair | amendment_pairs | one document per pair |
| `amendment_pairs` | amendment | Immutable pair of schedule versions | schedule_versions | one document per pair |
| `amendment_simulations` | amendment | Read-only impact simulations | amendment_pairs, patients | one document per simulation |
| `audit_events` | platform | Append-only audit trail | - | one document per event; metadata is small and redacted at the edge |
| `command_receipts` | platform | Proof an idempotency key produced one result | patients | one document per key |
| `compiled_schedule_artifacts` | authoring | Hash/version-bound validation cache | schedule_versions | one document per (schedule hash, validator, schema) |
| `cutover_cohorts` | migration | Pilot cohort cutover state with its (bounded) member list | trials, patients | one document per cohort; at most 200 members, enforced by the model and the validator |
| `evaluation_archives` | evaluation | Metadata of archived projections and holds | schedule_evaluations | one document per evaluation |
| `extraction_run_traces` | extraction | Provider-neutral trace of one run (keyed by the run id) | extraction_runs | one document per run, looked up by _id only |
| `extraction_runs` | extraction | One extraction run and its lease/ownership | protocol_versions | one document per run |
| `identity_extraction_results` | identity | Low-cost identifier extraction result | protocol_document_identities | one document per result |
| `identity_jobs` | identity | Resumable identity work items | protocol_document_identities | one document per job |
| `identity_reviews` | identity | Reviewer resolution of a quarantined upload | protocol_document_identities | one document per decision |
| `migration_runs` | migration | Resumable legacy-to-UCTSM migration run and cursor | trials | one document per run; per-patient detail lives in the source mappings |
| `migration_source_mappings` | migration | Legacy id to canonical id with source hash and parity verdict | patients | one document per legacy patient |
| `notification_preferences` | notification | Explicit patient consent, zone, delivery time and quiet hours (no defaults) | patients | one revision-guarded document per patient |
| `outbox` | platform | Transactional outbox with fenced leases | - | one document per message |
| `outbox_inbox` | platform | Consumer-side processed-message proof | outbox | one document per (consumer, message) |
| `parity_runs` | parity | Parity comparison runs | trials, patients | one document per run |
| `patient_activity_records` | patient | Intra-day activity entries | patients | one document per fact; the patient's history is a set of documents, never an array |
| `patient_anchors` | patient | Recorded anchor dates | patients | one document per fact; the patient's history is a set of documents, never an array |
| `patient_commands` | patient | Append-only typed patient commands | patients | one document per command; reversals are new documents |
| `patient_conditions` | patient | Condition facts with supersession | patients | one document per fact; the patient's history is a set of documents, never an array |
| `patient_confinement_episodes` | evaluation | Projected confinement episodes | schedule_evaluations | one document per episode per evaluation |
| `patient_event_occurrences` | patient | Occurrence facts keyed by the logical visit | patients, schedule_versions | one document per fact; the patient's history is a set of documents, never an array |
| `patient_events` | evaluation | Projected visits of one evaluation | schedule_evaluations | one document per visit per evaluation; superseded ones can be archived |
| `patient_schedule_assignments` | patient | Which schedule version a patient is assigned | patients, schedule_versions | one document per fact; the patient's history is a set of documents, never an array |
| `patient_schedules` | evaluation | The single mutable pointer to the current evaluation | patients, schedule_evaluations | one document per patient and version |
| `patient_states` | patient | Effective-dated state and transition facts | patients | one document per fact; the patient's history is a set of documents, never an array |
| `patient_unscheduled_visits` | patient | Unscheduled visits | patients | one document per fact; the patient's history is a set of documents, never an array |
| `patients` | patient | Native patient record, revision-guarded | trials | one document per patient |
| `protocol_document_identities` | identity | Identity evidence and hashes of an uploaded document | protocol_versions | one document per upload |
| `protocol_versions` | authoring | Immutable protocol document version | protocols | one document per uploaded version; file bytes are not stored here |
| `protocols` | authoring | Protocol of a trial | trials | one document per protocol |
| `reminder_notices` | notification | One reminder to deliver or withdrawn, keyed by patient and reminder key | patients, schedule_evaluations | one document per reminder |
| `schedule_claims` | authoring | Reviewed claims behind schedule rules | schedule_versions | one document per item of one schedule version; sizes bounded by the schedule itself |
| `schedule_definitions` | authoring | Schedule definition owning a version series | protocols | one document per definition |
| `schedule_evaluations` | evaluation | Immutable evaluation record with input/output hashes | patients, schedule_versions | one document per evaluation; projections live in their own collections |
| `schedule_events` | authoring | Event of a schedule version | schedule_versions | one document per item of one schedule version; sizes bounded by the schedule itself |
| `schedule_evidence` | authoring | Source evidence of a schedule version | schedule_versions | one document per item of one schedule version; sizes bounded by the schedule itself |
| `schedule_impact_proposals` | patient | Impact previews awaiting confirmation | patients | one document per proposal |
| `schedule_review_decisions` | authoring | Append-only review decisions | schedule_versions | one document per decision |
| `schedule_tracks` | authoring | Approved schedule tracks and their policies | schedule_versions | one document per item of one schedule version; sizes bounded by the schedule itself |
| `schedule_validation_issues` | authoring | Validator findings of a schedule version | schedule_versions | one document per item of one schedule version; sizes bounded by the schedule itself |
| `schedule_versions` | authoring | Immutable approved schedule content with hash and lineage | schedule_definitions, protocol_versions | content embeds one schedule; guarded by the 16 MiB document limit and the revision field |
| `trials` | authoring | Trial and its schedule read mode | - | one document per trial |
