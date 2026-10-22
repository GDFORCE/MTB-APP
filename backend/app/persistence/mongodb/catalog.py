"""The documented MongoDB collection architecture (P14-01).

One entry per collection: who owns it, what it holds, how it references others (always by
application UUID, never an ObjectId), and how a single document stays bounded. A test fails when a
migration creates a collection that is not described here, so the catalog cannot rot.
``python -m app.persistence.mongodb.catalog`` regenerates ``docs/uctsm-mongo-collections.md``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Collection:
    owner: str                    # bounded context / module that writes it
    purpose: str
    references: tuple[str, ...]   # other collections referenced by UUID field
    bounded: str                  # why one document cannot grow without limit
    scope: str = "organization_id"  # tenancy/scope field(s)


_PATIENT = "one document per fact; the patient's history is a set of documents, never an array"
_VERSION = "one document per item of one schedule version; sizes bounded by the schedule itself"

CATALOG: dict[str, Collection] = {
    # authoring plane
    "trials": Collection("authoring", "Trial and its schedule read mode", (), "one document per trial"),
    "protocols": Collection("authoring", "Protocol of a trial", ("trials",), "one document per protocol"),
    "protocol_versions": Collection("authoring", "Immutable protocol document version", ("protocols",), "one document per uploaded version; file bytes are not stored here"),
    "schedule_definitions": Collection("authoring", "Schedule definition owning a version series", ("protocols",), "one document per definition"),
    "schedule_versions": Collection("authoring", "Immutable approved schedule content with hash and lineage", ("schedule_definitions", "protocol_versions"), "content embeds one schedule; guarded by the 16 MiB document limit and the revision field"),
    "schedule_events": Collection("authoring", "Event of a schedule version", ("schedule_versions",), _VERSION),
    "schedule_evidence": Collection("authoring", "Source evidence of a schedule version", ("schedule_versions",), _VERSION),
    "schedule_claims": Collection("authoring", "Reviewed claims behind schedule rules", ("schedule_versions",), _VERSION),
    "schedule_validation_issues": Collection("authoring", "Validator findings of a schedule version", ("schedule_versions",), _VERSION),
    "schedule_review_decisions": Collection("authoring", "Append-only review decisions", ("schedule_versions",), "one document per decision"),
    "schedule_tracks": Collection("authoring", "Approved schedule tracks and their policies", ("schedule_versions",), _VERSION),
    "extraction_runs": Collection("extraction", "One extraction run and its lease/ownership", ("protocol_versions",), "one document per run"),
    "extraction_run_traces": Collection("extraction", "Provider-neutral trace of one run (keyed by the run id)", ("extraction_runs",), "one document per run, looked up by _id only"),
    "compiled_schedule_artifacts": Collection("authoring", "Hash/version-bound validation cache", ("schedule_versions",), "one document per (schedule hash, validator, schema)"),
    # patient plane
    "patients": Collection("patient", "Native patient record, revision-guarded", ("trials",), "one document per patient"),
    "patient_anchors": Collection("patient", "Recorded anchor dates", ("patients",), _PATIENT),
    "patient_states": Collection("patient", "Effective-dated state and transition facts", ("patients",), _PATIENT),
    "patient_conditions": Collection("patient", "Condition facts with supersession", ("patients",), _PATIENT),
    "patient_activity_records": Collection("patient", "Intra-day activity entries", ("patients",), _PATIENT),
    "patient_unscheduled_visits": Collection("patient", "Unscheduled visits", ("patients",), _PATIENT),
    "patient_event_occurrences": Collection("patient", "Occurrence facts keyed by the logical visit", ("patients", "schedule_versions"), _PATIENT),
    "patient_schedule_assignments": Collection("patient", "Which schedule version a patient is assigned", ("patients", "schedule_versions"), _PATIENT),
    "patient_commands": Collection("patient", "Append-only typed patient commands", ("patients",), "one document per command; reversals are new documents"),
    "schedule_evaluations": Collection("evaluation", "Immutable evaluation record with input/output hashes", ("patients", "schedule_versions"), "one document per evaluation; projections live in their own collections"),
    "patient_events": Collection("evaluation", "Projected visits of one evaluation", ("schedule_evaluations",), "one document per visit per evaluation; superseded ones can be archived"),
    "patient_confinement_episodes": Collection("evaluation", "Projected confinement episodes", ("schedule_evaluations",), "one document per episode per evaluation"),
    "patient_schedules": Collection("evaluation", "The single mutable pointer to the current evaluation", ("patients", "schedule_evaluations"), "one document per patient and version"),
    "evaluation_archives": Collection("evaluation", "Metadata of archived projections and holds", ("schedule_evaluations",), "one document per evaluation"),
    "schedule_impact_proposals": Collection("patient", "Impact previews awaiting confirmation", ("patients",), "one document per proposal"),
    "parity_runs": Collection("parity", "Parity comparison runs", ("trials", "patients"), "one document per run"),
    # amendments
    "amendment_pairs": Collection("amendment", "Immutable pair of schedule versions", ("schedule_versions",), "one document per pair"),
    "amendment_mapping_sets": Collection("amendment", "Reviewed event mapping of a pair", ("amendment_pairs",), "one document per pair"),
    "amendment_mapping_decisions": Collection("amendment", "Append-only mapping review decisions", ("amendment_mapping_sets",), "one document per decision"),
    "amendment_simulations": Collection("amendment", "Read-only impact simulations", ("amendment_pairs", "patients"), "one document per simulation"),
    "amendment_decisions": Collection("amendment", "Explicit patient adoption decisions", ("amendment_pairs", "patients"), "one document per decision"),
    # protocol identity
    "protocol_document_identities": Collection("identity", "Identity evidence and hashes of an uploaded document", ("protocol_versions",), "one document per upload"),
    "identity_reviews": Collection("identity", "Reviewer resolution of a quarantined upload", ("protocol_document_identities",), "one document per decision"),
    "identity_jobs": Collection("identity", "Resumable identity work items", ("protocol_document_identities",), "one document per job"),
    "identity_extraction_results": Collection("identity", "Low-cost identifier extraction result", ("protocol_document_identities",), "one document per result"),
    # cross-cutting
    "command_receipts": Collection("platform", "Proof an idempotency key produced one result", ("patients",), "one document per key"),
    "outbox": Collection("platform", "Transactional outbox with fenced leases", (), "one document per message"),
    "outbox_inbox": Collection("platform", "Consumer-side processed-message proof", ("outbox",), "one document per (consumer, message)"),
    "migration_runs": Collection("migration", "Resumable legacy-to-UCTSM migration run and cursor", ("trials",), "one document per run; per-patient detail lives in the source mappings"),
    "migration_source_mappings": Collection("migration", "Legacy id to canonical id with source hash and parity verdict", ("patients",), "one document per legacy patient"),
    "cutover_cohorts": Collection("migration", "Pilot cohort cutover state with its (bounded) member list", ("trials", "patients"), "one document per cohort; at most 200 members, enforced by the model and the validator"),
    "notification_preferences": Collection("notification", "Explicit patient consent, zone, delivery time and quiet hours (no defaults)", ("patients",), "one revision-guarded document per patient"),
    "reminder_notices": Collection("notification", "One reminder to deliver or withdrawn, keyed by patient and reminder key", ("patients", "schedule_evaluations"), "one document per reminder"),
    "audit_events": Collection("platform", "Append-only audit trail", (), "one document per event; metadata is small and redacted at the edge"),
    "_schema_migrations": Collection("platform", "Migration ledger (infrastructure; the only collection with ObjectId _id)", (), "one document per migration", scope="none"),
}

#: collections whose _id is not an application UUID (infrastructure only).
INFRASTRUCTURE = frozenset({"_schema_migrations"})


def markdown() -> str:
    lines = ["# UCTSM MongoDB collections", "",
             "Generated from `app/persistence/mongodb/catalog.py` (P14-01); do not edit by hand.", "",
             "Identity: every collection except the migration ledger uses an application-generated UUID "
             "(binary subtype 4) as `_id`; references are UUID fields, never ObjectIds.", "",
             "| Collection | Owner | Purpose | References | Bounded-document strategy |", "|---|---|---|---|---|"]
    for name, item in sorted(CATALOG.items()):
        refs = ", ".join(item.references) or "-"
        lines.append(f"| `{name}` | {item.owner} | {item.purpose} | {refs} | {item.bounded} |")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[4] / "docs" / "uctsm-mongo-collections.md"
    target.write_text(markdown(), encoding="utf-8")
    print(f"wrote {target}")
