"""Build a local source-reading companion without importing application modules.

Run from any directory: python <this file>. Only this documentation directory
is written. No network requests, model calls, database access or app execution.
"""
from __future__ import annotations

import ast
import hashlib
import html
import io
import json
from pathlib import Path
import subprocess
import tokenize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FILES = [
    ("backend/app/extraction/runner.py", "Pipeline orchestration", "Connects model reading, reconciliation and compilation; also runs queued jobs."),
    ("backend/app/extraction/documents.py", "Document loading", "Turns stored PDF/text bytes into numbered pages and model-visible text."),
    ("backend/app/extraction/contracts.py", "Pipeline boundary", "Defines DocumentPage and ExtractionResult: the inputs and outputs of extraction."),
    ("backend/app/extraction/facts.py", "Intermediate facts", "Defines typed facts, citations and the document-level intermediate representation."),
    ("backend/app/extraction/reader.py", "AI reader and prompts", "Selects and batches source pages, calls the model, validates facts and accounts for coverage."),
    ("backend/app/extraction/llm_client.py", "Provider transport", "Implements the common model-call contract for Anthropic, Gemini and OpenAI."),
    ("backend/app/extraction/reconcile.py", "Whole-document reconciliation", "Normalizes facts across batches without calling a model."),
    ("backend/app/extraction/compiler.py", "Facts to schedule", "Compiles cited facts into canonical schedule rules and review issues."),
    ("backend/app/domain/schedule/models.py", "Canonical data model", "Defines schedule definitions, patient context, issues and evaluation outputs."),
    ("backend/app/domain/schedule/timing.py", "Timing expression model", "Defines strict typed timing, references, boundaries and constraint expressions."),
    ("backend/app/domain/schedule/validator.py", "Schedule validation", "Checks rule integrity and reports blocking and nonblocking issues."),
    ("backend/app/application/extraction.py", "Extraction storage workflow", "Claims runs and persists draft schedule versions through repository ports."),
    ("backend/app/application/schedule_review.py", "Review and approval", "Applies reviewed changes and enforces approval gates in guarded writes."),
    ("backend/app/domain/schedule/evaluator.py", "Patient date engine", "Evaluates approved schedule rules against a patient's recorded context."),
    ("backend/app/application/patient_schedule.py", "Patient workflow", "Builds patient context, evaluates and persists projections and occurrence facts."),
    ("backend/app/application/schedule_projection.py", "Calendar and reminders", "Converts confirmed resolved occurrences into calendar entries and reminder candidates."),
    ("backend/server.py", "HTTP integration", "Contains upload/identity/consume/build endpoints and the rest of the platform's main API."),
    ("frontend/app/(app)/sponsor/add-trial.tsx", "Add Trial UI", "Connects protocol upload, extraction previews, trial saving and canonical draft building."),
    ("frontend/src/features/uctsm/api.ts", "Frontend API wrappers", "Defines typed request helpers for schedule review and patient operations."),
    ("backend/tests/test_extraction_reader.py", "Reader examples", "Uses scripted clients to check reader behavior without a live model."),
    ("backend/tests/test_extraction_compiler.py", "Compiler examples", "Demonstrates facts-to-schedule mappings and unresolved cases with assertions."),
    ("backend/app/extraction/artifacts.py", "Supporting: resumable artifacts", "Keys and stores model-call artifacts so completed questions can be reused."),
    ("backend/app/extraction/ledger.py", "Supporting: coverage accounting", "Defines source coverage states, statement dispositions and completeness summaries."),
    ("backend/app/extraction/statements.py", "Supporting: statement accountability", "Accounts for each source statement and blocks affected rules when statements remain uncompiled."),
    ("backend/app/services/native_import.py", "Supporting: SQL native import", "The SQL-session native importer selected by server.py's current Add Trial build helper."),
    ("backend/app/services/extraction_service.py", "Supporting: SQL extraction storage", "Persists extraction runs and draft schedules through SQLAlchemy sessions."),
    ("backend/app/services/schedule_service.py", "Supporting: SQL review and patients", "The SQL-backed review and patient workflow used alongside the Mongo repository-port implementation."),
    ("backend/app/api/uctsm.py", "Supporting: UCTSM API", "HTTP routes for SQL-session UCTSM authoring, review, evaluation and cutover."),
    ("backend/app/api/uctsm_mongo.py", "Supporting: Mongo plane API", "HTTP entry points to the explicitly configured Mongo patient plane and repository-port workflows."),
    ("backend/app/services/review_base.py", "SQL review: shared base", "Shared session/repository access and frozen-version guards for SQL review."),
    ("backend/app/services/review_editing.py", "SQL review: edits", "Implements draft validation, event corrections, footnote resolutions and review decisions."),
    ("backend/app/services/review_approval.py", "SQL review: approval", "Implements SQL-backed submission, approval gates, approval stamps and rejection."),
    ("backend/app/services/review_queue.py", "SQL review: inbox", "Queries the review queue and pending schedule versions."),
    ("backend/app/services/patient_schedule_reads.py", "SQL patient: reads", "Loads recorded facts, history and calendar data for SQL-backed patients."),
    ("backend/app/services/patient_schedule_context.py", "SQL patient: context", "Assembles patient-specific inputs for the shared deterministic evaluator."),
    ("backend/app/services/patient_schedule_persistence.py", "SQL patient: projection storage", "Writes evaluated occurrences, activities and confinements through SQL repositories."),
    ("backend/app/services/patient_schedule_recording.py", "SQL patient: actual facts", "Records patient occurrence/activity facts and validates actual dates."),
    ("backend/app/services/patient_schedule_evaluation.py", "SQL patient: evaluation", "Coordinates SQL-backed evaluation, idempotency, history preservation and recorded actuals."),
]

# These notes explain the application-level purpose, in addition to syntax.
# Unlisted symbols retain their source docstring and exact statement explanations;
# this is intentionally not a guessed account of business intent.
NOTES = {
    "runner.py": {
        "configured_provider": "Reads the explicitly selected provider and checks that provider's key is present. Merely having another provider's key does not select extraction.",
        "engine_signature": "Builds the compatibility identity for reusable results from provider, configured model string and prompt/reconciler/compiler versions. None disables compatibility reuse.",
        "_client_for": "Current implementation passes PDF bytes to the chosen client for all PDF reads, including PDFs with embedded text; do not infer text-only behavior from older docstrings.",
        "extract_protocol": "The central sequence: read_document -> reconcile -> compile_document. It prepends source trace entries, attaches merged accountability and returns trial metadata, facts and schedule results.",
        "build_accountability": "Joins reader source coverage with compiler statement dispositions. A successful model reply is not itself a completeness claim: coverage and uncompiled statements are checked separately.",
        "execute_run": "SQL-session worker retained alongside the repository-port worker. It claims and commits before loading/calling the model, then commits drafts or records failure.",
        "execute_run_on_ports": "Repository-port worker: claims a leased run, renews the lease on a heartbeat, loads and identity-checks the source, offloads extraction, then completes with the lease token. A worker that lost ownership cannot overwrite another worker's outcome.",
        "extract_trial_details": "Reads Add Trial metadata only. This does not run the whole facts/reconciliation/schedule compiler.",
    },
    "documents.py": {
        "pages_from_pdf": "Extracts embedded text page by page. Suspected broken-font text is classified for image reading. The original one-based physical PDF page number is retained for evidence.",
        "looks_scrambled": "Uses a Latin-script vowel-frequency heuristic, not a language-independent readability proof. Fewer than 20 candidate words do not trigger it.",
        "document_text": "Adds explicit physical-page markers and image-reading notices. Exceeding the text budget raises an error instead of silently truncating later pages.",
        "load_pages": "Reads source bytes from storage and returns both parsed pages and original PDF bytes when applicable. Text uploads return None for the PDF part.",
    },
    "facts.py": {
        "FactModel": "Preserves extra model keys so the boundary can report unrecognized information rather than silently discard it. Preservation does not make those keys executable.",
        "ScheduleFact": "One intermediate obligation or anchor/condition statement, with source evidence. Missing timing/mode/window fields can be represented here; later compilation and validation determine readiness.",
        "Citation": "The reader, not the model, sets verification against source text. A real quote proves provenance, not necessarily correct clinical interpretation.",
        "DocumentFacts": "One document's typed metadata, dimensions, treatment blocks, facts, reader issues, trace and accountability. It has no per-patient schedule dates.",
        "Duration.from_text": "Accepts stated duration text such as '-7 days' and normalizes known unit spellings; unrecognized amounts raise validation errors.",
        "unknown_fields": "Finds extra fields recursively. This is how unrecognized model output can remain visible to review.",
    },
    "reader.py": {
        "ReaderLimits.from_env": "The product environment path defaults to reading all pages, within configured budgets. Direct ReaderLimits() has read_all_pages=False; do not conflate these two construction paths.",
        "_Run.check_budget": "Enforces technical call/time budgets before further calls. These are extraction-resource limits, not clinical recurrence limits.",
        "plan_pages": "Builds selected/not-selected/over-budget page lists. Depending on limits it reads all eligible pages or ranks keyword matches; image pages and table continuations have explicit handling.",
        "_ask": "Looks up a completed artifact for the exact question before spending a model call. Stores attempts, parses replies and allows one parse-repair retry. Transport errors are recorded and propagated.",
        "verify_citation": "Checks cited page membership and quote text, producing TEXT_MATCHED, UNVERIFIED or REJECTED. Image-only evidence cannot be proven by an embedded-text comparison.",
        "_read_batch": "Parses one batch's facts, table cells and footnotes; validates each item, records rejected/unknown information and verifies evidence. Raw failures remain accountable.",
        "_cells_to_facts": "Turns marked schedule-table cells into activity facts tied to the stated visits. This provides structured table information alongside narrative facts.",
        "_repair_invalid_facts": "Requests one bounded correction of malformed facts. Invalid facts that cannot be corrected remain rejected rather than becoming assumed valid.",
        "_follow_references": "Follows source section/appendix references under depth/page limits. A referenced source that cannot be read is represented in coverage instead of silently treated as absent.",
        "read_document": "Initializes budgets and coverage; identifies readable pages; reports embedded instruction text; handles amendment-history exclusions; reads metadata and page batches; accounts for failures; returns DocumentFacts. Partial useful reads can survive with blocking issues.",
        "_coverage_issues": "Converts unread material, missing required references and interrupted reading into explicit review findings. Not every diagnostic has blocking=True; inspect the constructed issue.",
        "_finish_manifest": "Writes a resumable manifest with source hashes, executed stages and usage counters. Durable storage failure is surfaced instead of changing completed answers into new facts.",
    },
    "llm_client.py": {
        "DocumentContextClient.for_document": "Creates a scoped client with only the selected original pages. The packet includes a mapping from packet positions to physical PDF page numbers.",
        "AnthropicClient.complete": "Sends the reader's prompt through Anthropic Messages, including source context; extracts returned text and translates provider failure into ExtractionModelError.",
        "GeminiClient.complete": "Sends the same reader question through Google's SDK and can attach native PDF content. This is a transport alternative for the same pipeline.",
        "OpenAIClient.complete": "Uses Chat Completions with a JSON-object response request; when present, the scoped PDF is attached as file content. Provider errors do not become an empty successful extraction.",
        "parse_json_object": "Tolerates presentation noise such as code fences/prefaces, but requires a JSON object; it does not prove that the object matches the facts schema.",
    },
    "reconcile.py": {
        "anchor_key": "Normalizes wording wrappers, punctuation and case for matching. It does not equate arbitrary clinical concepts with different names.",
        "reconcile": "Copies the document, creates stable source ordering, records duplicate relations, canonicalizes recognized anchor aliases, reports unmatched names and separates/groups occurrences using explicit context. No model call occurs.",
    },
    "compiler.py": {
        "_Compiler.compile": "Runs name/dimension/cycle/event/activity/condition/anchor/process construction, creates an EXTRACTED UniversalSchedule, validates it and builds a statement ledger. Returns the draft plus issues and trace; does not approve it or calculate patient dates.",
        "compile_document": "Compiles independent schedule options separately. Shared facts are available to each option; unknown option labels are retained for review with an explicit issue instead of dropped.",
        "_Compiler.compile_timing": "Dispatches a stated timing pattern into typed timing expressions. Required missing amounts/references produce unresolved behavior instead of invented timing.",
        "_Compiler.reference": "Resolves an explicitly named dependency to an anchor/event/condition reference using collected symbols. This is reference resolution, not patient-date lookup.",
        "_Compiler._build_cycle_plan": "Combines explicitly stated cycle-plan properties from facts and treatment-block metadata. Inconsistent or unstated properties become review issues.",
        "_Compiler._phased": "Builds one segmented recurrence plan for phased follow-up. Segment boundaries and inclusivity come from source information, not an arbitrary default.",
        "_Compiler._build_group": "Combines candidate readings of one occurrence, identifies conflicts/combined restrictions, and attaches provenance. It does not blindly keep the first fact.",
        "_Compiler._table_precedence": "A narrow special case for one exact table reading and narrative summaries of that same timepoint. It is not a general rule that every table overrides narrative.",
        "_Compiler._restriction_nodes": "Preserves additional restrictions as constraint nodes and tracks statements that could not be compiled. Retaining a node does not mean an operator is supported.",
        "_Compiler.build_conditions": "Compiles condition-dependent changes such as hold, resume or repeat into explicit schedule behavior where source facts are sufficient.",
        "_Compiler.finish_anchors": "Examines references to anchors not fully defined by a source fact; keeps missing definitions visible as review requirements.",
        "_Compiler.reader_issues": "Carries reader-origin issues into schedule validation, scoped to an affected entity when possible.",
    },
    "models.py": {
        "UniversalSchedule": "Canonical definition containing metadata, anchors, events, dimensions, conditional rules, processes, evidence and validation issues. A definition is not an individual patient's generated calendar.",
        "PatientContext": "The patient-specific inputs for evaluation: pinned version, known/planned dates, assignments and recorded facts/state. Availability of inputs determines what can resolve.",
        "EvaluatedEvent": "One resulting occurrence with status, resolved timing when available, activities and an explanation. A blocked/waiting occurrence can exist without a date.",
        "EvaluationResult": "The result envelope with patient/version/evaluator identity, input snapshot, generated occurrences, confinements and evaluation metadata.",
    },
    "validator.py": {
        "ScheduleValidator": "Checks structural and semantic integrity without consulting a model. Follow validate() to see the actual rule checks and blocking() to see which findings block readiness.",
    },
    "extraction.py": {
        "ExtractionService._persist_one": "Deep-copies a compiled schedule, assigns fresh issue IDs, marks it VALIDATION_REQUIRED, resolves/creates its definition and persists a new version. This is the handoff from in-memory result to stored draft.",
        "ExtractionService.complete_in": "Checks run ownership and state; persists usable schedule options; attaches traces/accountability; rejects empty accountable schedules; commits COMPLETED/PARTIAL/FAILED state through the caller's transaction.",
        "ExtractionService.start": "Claims a queued/recoverable extraction attempt using a lease token. Workers must prove ownership before completing/failing it.",
        "import_native_schedules": "Imports already-computed native extraction payloads through repository ports in one unit of work. It does not rerun the LLM to build a stored draft.",
    },
    "schedule_review.py": {
        "approval_blockers": "Uses the review projection to gather pending row, source coverage and statement-disposition blockers, in addition to the validator's rule findings.",
        "ScheduleReviewService.correct_event": "Applies an explicit reviewer correction to an editable draft and records the decision/audit; approved versions are frozen.",
        "ScheduleReviewService.resolve_qualifier": "Turns an extracted footnote into a recorded reviewer decision and, where supported, explicit schedule meaning.",
        "ScheduleReviewService._approve": "Requires IN_REVIEW, enforces maker/checker policy, checks manual decisions and required inputs, reruns validation, checks approval blockers and required field confirmations, then stamps approval hashes in a revision-guarded write and stores evaluator compilation metadata.",
    },
    "evaluator.py": {
        "ScheduleEvaluator.evaluate": "Rejects unapproved or mismatched versions; obtains dependency order; plans reuse; localizes context; builds conditional/process/assignment state; evaluates events in dependency order; derives anchors; applies rolling materialization; returns explained occurrences and confinements.",
        "evaluate_timing": "Resolves one typed timing expression using the available context. Missing anchor/dependency data is represented as waiting/unresolved behavior, not filled from today's date.",
        "nth_occurrence": "Calculates occurrence n relative to the original start so a month-end clamp in one occurrence does not permanently shift later months.",
        "_apply_rolling_horizon": "Limits what is materialized for the current projection. It does not convert the technical projection horizon into the clinical end of recurrence.",
        "cycle_starts": "Calculates each cycle's start using its explicit dependency mode and known actuals up to termination/horizon. Planned starts are not automatically actual dates.",
        "ScheduleEvaluator._conditional_gate": "Decides whether a conditional occurrence is active before calculating its date. An untriggered visit cannot acquire an artificial due date.",
        "ScheduleEvaluator._expand_recurrence": "Expands a repeat rule into indexed occurrences using stated cadence, termination, dependency and horizon behavior.",
        "ScheduleEvaluator._expand_recurrence_plan": "Expands phased follow-up as one indexed series across stated segment boundaries.",
        "ScheduleEvaluator._expand_cycle_days": "Dates Cycle-N-Day-D from that cycle's own start, preserving cycle occurrence identity and activity restrictions.",
        "ScheduleEvaluator._expand_calendar_recurrence": "Expands explicit weekday-series policies using the calendar recurrence operators and process holds.",
        "ScheduleEvaluator._derive_event_anchors": "Publishes event-derived reference dates while keeping actual and merely planned dates separate.",
        "ScheduleEvaluator._evaluate_with_history": "Evaluates occurrences using assignments in force on their dates and distinct condition episodes, rather than applying the latest assignment to all history.",
    },
    "patient_schedule.py": {
        "PatientScheduleService.build_context": "Loads and normalizes the patient's anchors, occurrence history, condition state, assignments and other facts into a PatientContext for one schedule version.",
        "PatientScheduleService.evaluate_result": "Loads the canonical schedule, verifies manual handling, gets compiled metadata, builds context and calls evaluation through the memoization layer. The imported evaluator is not necessarily called directly on every request.",
        "PatientScheduleService._evaluate": "Performs evaluation inside a unit of work, reconciles immutable projections with protected history, persists the result/pointer and appends the evaluation audit record.",
        "PatientScheduleService.reconcile_projection": "Pure reconciliation shared with preview: preserves protected outcomes/history while applying new calculated future occurrences.",
        "PatientScheduleService.record_occurrence_fact": "Records a performed/missed/booked fact against a logical occurrence. A booking is not an actual clinical event and a passed date is not proof of completion.",
        "PatientScheduleService.record_actual_occurrence": "Validates actual dates, appends the occurrence fact, audits and reevaluates dependent dates atomically where required.",
        "PatientScheduleService.version_lineage": "Uses explicit reviewed mappings to decide which historical facts can carry across schedule versions; equal event codes alone are insufficient.",
    },
    "schedule_projection.py": {
        "project": "Creates patient-facing entries only from eligible resolved dated occurrences. Applies attendance wording and confinement grouping; suppressed/undated items do not become reminders.",
        "reconcile": "Diffs existing reminder keys against desired dated candidates. A moved date requires the old key to be cancelled and a new dated key to be scheduled.",
    },
    "server.py": {
        "_extract_for_identity": "After identity clearance, claims/reuses an extraction keyed by organization/content/engine signature; runs extraction in a thread pool; stores the source and result; materializes draft extraction entries. Conflicting in-progress work returns 409.",
        "_run_protocol_extraction": "Validates/parses uploaded content and resolves identity before invoking the full extraction path.",
        "extract_protocol_details": "Metadata-only upload endpoint. A provider configuration failure is 503; it is not evidence that every configured full extraction request always returns 503.",
        "extract_protocol_alias": "Full protocol upload endpoint used by Add Trial. Delegates to shared identity-gated extraction helpers.",
        "consume_protocol_extraction": "Attaches an extracted option to a specific authorized trial without reading the model again.",
        "_import_canonical_schedules_sync": "Selects native import when native payloads are present; otherwise selects the legacy canonical-plan bridge. It imports stored definitions, not a second LLM extraction.",
        "build_canonical_schedule": "Loads the trial and stored definitions, checks access, imports canonical drafts, updates linkage and returns version IDs. It does not approve or automatically cut over every trial's reads.",
    },
}


def clip(value: str, limit: int = 380) -> str:
    value = " ".join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def src(node: ast.AST | None) -> str:
    return clip(ast.unparse(node)) if node is not None else "None"


def value(node: ast.AST | None, depth: int = 0) -> str:
    """Explain expression evaluation without inventing a callee's behavior."""
    if node is None:
        return "None"
    if depth > 2:
        return src(node)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return f"the literal text {clip(repr(node.value), 160)}"
        return repr(node.value)
    if isinstance(node, ast.Name):
        return f"the current value of {node.id}"
    if isinstance(node, ast.Attribute):
        return f"attribute {src(node)}"
    if isinstance(node, ast.Await):
        return f"the result obtained by awaiting {src(node.value)}"
    if isinstance(node, ast.Call):
        callee = src(node.func)
        args = ", ".join([src(a) for a in node.args] +
                         [(k.arg + "=" if k.arg else "**") + src(k.value) for k in node.keywords])
        if callee.endswith(".get") and node.args:
            return f"the lookup {callee}({args}); a missing key uses the supplied fallback or None"
        if callee.endswith(".model_copy"):
            return f"a Pydantic model copy from {callee}({args}); deep=True also copies nested values"
        if callee.endswith(".model_validate"):
            return f"the typed model validated by {callee}({args}); invalid input raises validation errors"
        if callee.endswith(".model_dump"):
            return f"the serialized model data from {callee}({args})"
        if callee == "Field":
            return f"Pydantic field configuration Field({args}); defaults/factories and constraints are declared here"
        if callee == "os.getenv":
            return f"the environment lookup os.getenv({args}); this does not establish the deployed value"
        return f"the result of calling {callee}({clip(args, 270)})"
    if isinstance(node, ast.BoolOp):
        op = "first truthy operand, or the final operand" if isinstance(node.op, ast.Or) else "first falsy operand, or the final operand"
        return f"{src(node)} (short-circuit selection: {op})"
    if isinstance(node, ast.IfExp):
        return f"{src(node.body)} when {src(node.test)} is true, otherwise {src(node.orelse)}"
    if isinstance(node, ast.Dict):
        return f"a dictionary built from {src(node)}; ** entries merge mappings and later keys take precedence"
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return f"a {type(node).__name__.lower()} containing {src(node)}"
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        generators = "; ".join(f"bind {src(g.target)} from {src(g.iter)}" +
                               (" when " + " and ".join(src(c) for c in g.ifs) if g.ifs else "") for g in node.generators)
        item = f"{src(node.key)}: {src(node.value)}" if isinstance(node, ast.DictComp) else src(node.elt)
        return f"a {'lazy generator' if isinstance(node, ast.GeneratorExp) else 'collection'} of {item}; {generators}"
    if isinstance(node, ast.Compare):
        return f"the boolean comparison {src(node)}"
    if isinstance(node, ast.JoinedStr):
        return f"formatted text {src(node)} with expressions interpolated at runtime"
    if isinstance(node, ast.Subscript):
        return f"the indexed/sliced value {src(node)}"
    if isinstance(node, ast.Lambda):
        return f"a callable {src(node)}; its expression runs when invoked"
    return src(node)


def explain(node: ast.AST, parent: ast.AST | None) -> str:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        prefix = "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
        return f"Define {prefix} {node.name}({src(node.args)}). Defining it does not execute its body. " + (f"Return annotation: {src(node.returns)}." if node.returns else "")
    if isinstance(node, ast.ClassDef):
        return f"Define class {node.name}" + (f" inheriting from {', '.join(src(b) for b in node.bases)}" if node.bases else "") + ". Its class body runs while the class is created; methods run when called."
    if isinstance(node, ast.ImportFrom):
        return f"Import {', '.join(a.name + (' as ' + a.asname if a.asname else '') for a in node.names)} from {'.' * node.level + (node.module or '')}. Relative dots resolve from this package."
    if isinstance(node, ast.Import):
        return f"Import {', '.join(a.name + (' as ' + a.asname if a.asname else '') for a in node.names)} and bind its module name/alias. Importing a module can run its top-level code."
    if isinstance(node, ast.Assign):
        return f"Assign {value(node.value)} to {', '.join(src(t) for t in node.targets)}."
    if isinstance(node, ast.AnnAssign):
        owner = "model/class field" if isinstance(parent, ast.ClassDef) else "variable"
        default = f" Initialize it with {value(node.value)}." if node.value is not None else " No Python value is assigned here; a model framework can interpret this as a required field."
        return f"Declare {owner} {src(node.target)} with annotation {src(node.annotation)}.{default}"
    if isinstance(node, ast.AugAssign):
        return f"Update {src(node.target)} using {type(node.op).__name__} with {value(node.value)} ({src(node)})."
    if isinstance(node, ast.Return):
        return f"Return {value(node.value)} to the caller and stop this invocation."
    if isinstance(node, ast.If):
        return f"Test {src(node.test)}. Execute the indented body only when truthy; otherwise execute its else/elif path when present."
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return f"Iterate {src(node.iter)}, binding each item to {src(node.target)} and executing the body. An else suite, if present, runs when the loop finishes without break."
    if isinstance(node, ast.While):
        return f"Repeat the body while {src(node.test)} is truthy."
    if isinstance(node, (ast.With, ast.AsyncWith)):
        items = ", ".join(src(i.context_expr) + (f" as {src(i.optional_vars)}" if i.optional_vars else "") for i in node.items)
        return f"Enter context manager(s) {items}. Their exit hooks run when leaving the body, including on exceptions; transaction behavior depends on the manager."
    if isinstance(node, (ast.Try, ast.TryStar)):
        return "Enter an exception-handled region. Matching except handlers process failures; else runs on success and finally runs on exit when provided."
    if isinstance(node, ast.ExceptHandler):
        return f"Handle exceptions matching {src(node.type) if node.type else 'any exception'}" + (f", bound as {node.name}" if node.name else "") + "."
    if isinstance(node, ast.Raise):
        return f"Raise {value(node.exc) if node.exc else 'the currently handled exception'}" + (f" with explicit cause {src(node.cause)}" if node.cause else "") + "; transfer control to exception handling."
    if isinstance(node, ast.Assert):
        return f"Assert {src(node.test)}; failure raises AssertionError" + (f" with message {src(node.msg)}" if node.msg else "") + ". Assertions can be disabled by optimized Python execution."
    if isinstance(node, ast.Expr):
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return "Documentation/string expression; it does not execute the workflow described in its text."
        if isinstance(node.value, (ast.Yield, ast.YieldFrom)):
            return f"Yield {src(node.value)} to the generator's consumer and suspend execution until resumed."
        return f"Evaluate {value(node.value)} for its effects; discard the expression's result here."
    if isinstance(node, ast.Delete):
        return f"Delete {', '.join(src(t) for t in node.targets)} from its binding/container."
    if isinstance(node, ast.Continue):
        return "Skip the rest of this loop iteration and proceed to the next iteration."
    if isinstance(node, ast.Break):
        return "Exit the nearest loop immediately; a loop else suite does not run."
    if isinstance(node, ast.Pass):
        return "No-operation placeholder; execution proceeds to the next statement."
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return f"Resolve {', '.join(node.names)} in the {'module' if isinstance(node, ast.Global) else 'enclosing function'} scope for assignments in this function."
    if isinstance(node, ast.Match):
        return f"Pattern-match {src(node.subject)} and run the first case whose pattern and guard match."
    return f"{type(node).__name__} syntax: {src(node)}."


def python_annotations(path: Path) -> dict:
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    statements = [n for n in ast.walk(tree) if isinstance(n, (ast.stmt, ast.ExceptHandler))]
    contexts = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    doc_lines = {}
    for n in [tree, *contexts]:
        body = getattr(n, "body", [])
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            for number in range(body[0].lineno, body[0].end_lineno + 1):
                doc_lines[number] = n
    strings = {}
    comments = {}
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.STRING and token.end[0] > token.start[0]:
            for number in range(token.start[0], token.end[0] + 1):
                strings[number] = token.start[0]
        if token.type == tokenize.COMMENT:
            comments[token.start[0]] = token.string.lstrip("#:").strip()
    decorators = {}
    for n in contexts:
        for d in n.decorator_list:
            for number in range(d.lineno, d.end_lineno + 1):
                decorators[number] = (n, d)
    by_line = [[] for _ in lines]
    scopes = [[] for _ in lines]
    for n in statements:
        for number in range(n.lineno, min(n.end_lineno, len(lines)) + 1):
            by_line[number - 1].append(n)
    for n in contexts:
        start = min([n.lineno] + [d.lineno for d in n.decorator_list])
        for number in range(start, min(n.end_lineno, len(lines)) + 1):
            scopes[number - 1].append(n)
    def qname(n):
        names = [n.name]
        ancestor = parents.get(n)
        while ancestor is not None:
            if isinstance(ancestor, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(ancestor.name)
            ancestor = parents.get(ancestor)
        return ".".join(reversed(names))
    notes = NOTES.get(path.name, {})
    symbols = [{"name": qname(n), "start": n.lineno, "end": n.end_lineno,
                "kind": type(n).__name__, "doc": ast.get_docstring(n) or "",
                "note": notes.get(qname(n), notes.get(n.name, ""))} for n in contexts]
    annotated = []
    for number, line in enumerate(lines, 1):
        candidates = sorted(by_line[number - 1], key=lambda n: (n.end_lineno - n.lineno, -n.lineno, n.col_offset))
        owner = candidates[0] if candidates else None
        scope_nodes = sorted(scopes[number - 1], key=lambda n: n.lineno)
        scope = qname(scope_nodes[-1]) if scope_nodes else "module"
        kind = type(owner).__name__ if owner else "formatting"
        if number in doc_lines:
            note = "Source docstring: documents " + (qname(doc_lines[number]) if doc_lines[number] is not tree else "this module") + ". It is documentation, not execution; historical descriptions can lag the implementation."
        elif number in strings and owner is not None and owner.lineno < number:
            target = ", ".join(src(t) for t in owner.targets) if isinstance(owner, ast.Assign) else scope
            note = f"Literal string content starting at L{strings[number]}, used by {target}. This text is data; Python does not execute instructions written inside it."
        elif not line.strip():
            note = "Blank line for readability; no runtime effect."
        elif line.lstrip().startswith("#"):
            note = "Source comment: " + comments.get(number, line.lstrip(" #")) + ". Comments have no runtime effect; verify intent against the code."
        elif number in decorators:
            n, d = decorators[number]
            note = f"Apply decorator {src(d)} to {qname(n)} when defining it. Decorators can register routes, validators, static/class methods or wrappers."
        elif line.strip().startswith("else:"):
            note = "Alternative suite of the enclosing if/loop/try. For if it handles a false condition; for loop/try its success semantics depend on the enclosing construct."
        elif line.strip().startswith("finally:"):
            note = "Cleanup suite of the enclosing try; runs when control exits the protected region, including exceptions/returns."
        elif owner is None:
            note = "Structural syntax; read together with the adjacent statement."
        else:
            note = explain(owner, parents.get(owner))
            if owner.lineno < number:
                note = f"Continuation of {type(owner).__name__} at L{owner.lineno}. " + note
            elif isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                note += " " + notes.get(qname(owner), notes.get(owner.name, ""))
        annotated.append({"n": number, "code": line, "scope": scope, "kind": kind, "note": note.strip()})
    return {"lines": annotated, "symbols": sorted(symbols, key=lambda n: n["start"]), "parser": "Python ast + tokenize"}


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>MTB extraction and schedule source walkthrough</title>
<style>
:root{color-scheme:light dark;font-family:system-ui,sans-serif;line-height:1.55;--border:#8885;--accent:#1976b8}*{box-sizing:border-box}body{margin:0}header{padding:22px 28px;border-bottom:1px solid var(--border)}h1{font-size:1.5rem;margin:0 0 6px}p{max-width:1050px;margin:8px 0}a{color:var(--accent)}.layout{display:grid;grid-template-columns:290px 1fr}nav{padding:18px;border-right:1px solid var(--border);height:calc(100vh - 160px);overflow:auto;position:sticky;top:0}button,input,select{font:inherit;padding:7px 10px;border:1px solid var(--border);border-radius:6px;background:transparent;color:inherit}button{cursor:pointer}button:focus-visible,a:focus-visible{outline:3px solid var(--accent)}nav button{display:block;width:100%;text-align:left;margin-bottom:6px;font-size:.85rem}nav button.active{border-color:var(--accent);font-weight:700}main{padding:20px;min-width:0}#toolbar{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}#search{flex:1;min-width:180px}#jump{width:110px}#symbol{max-width:100%}#meta{font-size:.88rem;overflow-wrap:anywhere}#symbols{padding:14px;border:1px solid var(--border);border-radius:8px;margin:10px 0}#symbols p{white-space:pre-wrap}table{width:100%;border-collapse:collapse;table-layout:fixed}th{text-align:left}th,td{padding:9px;border:1px solid var(--border);vertical-align:top}th:first-child,td:first-child{width:65px}th:nth-child(2){width:50%}pre{margin:0;font:12px/1.6 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}td small{display:block;opacity:.72;margin-bottom:5px;overflow-wrap:anywhere}td:last-child{font-size:.88rem;overflow-wrap:anywhere}.pager{display:flex;gap:12px;align-items:center;margin:12px 0}.highlight{background:#e6ad3125}#notice{font-size:.86rem;opacity:.86}#coverage{font-size:.85rem}@media(max-width:900px){.layout{display:block}nav{height:auto;position:static;max-height:230px;border-right:0;border-bottom:1px solid var(--border)}th:nth-child(2){width:45%}main{padding:10px}header{padding:16px}th,td{padding:5px}}
</style></head><body>
<header><h1>MTB: extraction → schedule rules → patient dates</h1>
<p>Every physical source line in the 21 files from the reading list plus supporting extraction, API and service implementation files, with statement explanations, symbol navigation and file-specific notes.</p>
<p id="notice">Snapshot: 22 October 2026, current local working tree. Line notes are generated static reading aids, not execution traces or proof of runtime correctness. Comments/docstrings are labeled as source documentation. Multiline statements share one explanation; source text remains exact. The Markdown guide provides the application-level explanation.</p>
<a href="README.md">Open the written guide</a> · <a href="file-guide.md">Open the complete symbol directory</a> · <span id="coverage"></span></header>
<div class="layout"><nav aria-label="Source files" id="files"></nav><main>
<h2 id="title"></h2><p id="purpose"></p><p id="meta"></p>
<label>Function/class/type <select id="symbol"><option value="">Choose a symbol</option></select></label>
<div id="symbols" hidden></div>
<div id="toolbar"><input id="search" aria-label="Search this file" placeholder="Search source, explanation or symbol in this file"><input id="jump" aria-label="Line number" type="number" min="1" placeholder="Line"><button id="go">Go to line</button><button id="clear">Clear filter</button></div>
<div class="pager"><button id="prev">Previous</button><span id="range" aria-live="polite"></span><button id="next">Next</button></div>
<table><thead><tr><th>Line</th><th>Exact source</th><th>Explanation</th></tr></thead><tbody id="rows"></tbody></table>
</main></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('payload').textContent);
const $=id=>document.getElementById(id), size=160;
let selected=0,offset=0,highlight=0;
function textElement(tag,value){const el=document.createElement(tag);el.textContent=value;return el;}
function setHash(line){history.replaceState(null,'','#file='+selected+(line?'&line='+line:''));}
function filtered(){const q=$('search').value.toLowerCase().trim();return data[selected].lines.filter(l=>!q||[l.code,l.note,l.scope].some(v=>v.toLowerCase().includes(q)));}
function draw(){const lines=filtered(),page=lines.slice(offset,offset+size);$('rows').replaceChildren();for(const l of page){const tr=document.createElement('tr');tr.id='L'+l.n;if(l.n===highlight)tr.className='highlight';const td=textElement('td','');const link=textElement('a',String(l.n));link.href='#file='+selected+'&line='+l.n;link.onclick=e=>{e.preventDefault();highlight=l.n;setHash(l.n);draw();};td.append(link);const code=document.createElement('td');code.append(textElement('pre',l.code||' '));const explanation=document.createElement('td');explanation.append(textElement('small',l.scope+' · '+l.kind),textElement('span',l.note));tr.append(td,code,explanation);$('rows').append(tr);}$('range').textContent=lines.length?`${offset+1}–${Math.min(offset+size,lines.length)} of ${lines.length} lines`:'No matching lines';$('prev').disabled=offset===0;$('next').disabled=offset+size>=lines.length;}
function choose(index,line=0){selected=Math.max(0,Math.min(data.length-1,index));offset=line?Math.floor((line-1)/size)*size:0;highlight=line;$('search').value='';const f=data[selected];$('title').textContent=`${selected+1}. ${f.title}`;$('purpose').textContent=f.purpose;$('meta').textContent=`${f.path} · ${f.lines.length} lines · parser: ${f.parser} · source SHA-256: ${f.sha256}`;$('symbol').replaceChildren(textElement('option','Choose a symbol'));$('symbol').firstChild.value='';for(let i=0;i<f.symbols.length;i++){const s=f.symbols[i],o=textElement('option',`${s.name} (L${s.start}–${s.end})`);o.value=i;$('symbol').append(o);}$('symbols').hidden=true;[...$('files').children].forEach((b,i)=>b.classList.toggle('active',i===selected));setHash(line);draw();}
function jump(line){line=Math.max(1,Math.min(data[selected].lines.length,Number(line)||1));$('search').value='';offset=Math.floor((line-1)/size)*size;highlight=line;setHash(line);draw();document.getElementById('L'+line)?.scrollIntoView({block:'center'});}
for(let i=0;i<data.length;i++){const b=textElement('button',`${i+1}. ${data[i].title}`);b.onclick=()=>choose(i);$('files').append(b);}
$('coverage').textContent=`${data.length} files · ${data.reduce((n,f)=>n+f.lines.length,0).toLocaleString()} source lines`;
$('search').oninput=()=>{offset=0;draw();};$('prev').onclick=()=>{offset=Math.max(0,offset-size);draw();};$('next').onclick=()=>{offset+=size;draw();};$('go').onclick=()=>jump($('jump').value);$('jump').onkeydown=e=>{if(e.key==='Enter')jump($('jump').value);};$('clear').onclick=()=>{$('search').value='';offset=0;draw();};
$('symbol').onchange=()=>{if($('symbol').value==='')return;const s=data[selected].symbols[Number($('symbol').value)];jump(s.start);$('symbols').replaceChildren(textElement('strong',s.name),textElement('p',s.note||'No separately authored application-level note for this symbol. Read its source and statement explanations.'),textElement('p',s.doc?'Source docstring (may contain historical descriptions):\n'+s.doc:'No source docstring.'));$('symbols').hidden=false;};
function fromHash(){const p=new URLSearchParams(location.hash.slice(1));choose(Number(p.get('file'))||0,Number(p.get('line'))||0);}window.addEventListener('hashchange',fromHash);fromHash();
</script></body></html>'''


def main():
    ts_files = [f for f, _, _ in FILES if f.endswith((".ts", ".tsx"))]
    ts = subprocess.run(["node", str(HERE / "annotate_typescript.cjs"), str(ROOT)],
                        input=json.dumps(ts_files), capture_output=True, text=True, encoding="utf-8", check=True)
    ts_results = json.loads(ts.stdout)
    records = []
    for index, (relative, title, purpose) in enumerate(FILES, 1):
        path = ROOT / relative
        result = python_annotations(path) if path.suffix == ".py" else ts_results[relative]
        assert len(result["lines"]) == len(path.read_text(encoding="utf-8-sig").splitlines()), relative
        assert all(row["n"] == n and row["note"] for n, row in enumerate(result["lines"], 1)), relative
        record = {"path": relative, "title": title, "purpose": purpose,
                  "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), **result}
        records.append(record)
    payload = json.dumps(records, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    (HERE / "walkthrough.html").write_text(PAGE.replace("__PAYLOAD__", payload), encoding="utf-8")
    manifest = {"snapshot_date": "2026-10-22", "scope": "21 files from the prior reading list plus supporting extraction/API/service implementation files; entire files, including server.py",
                "annotation_method": "Static syntax explanations plus separately authored application-level symbol notes",
                "total_lines": sum(len(f["lines"]) for f in records),
                "files": [{"path": f["path"], "sha256": f["sha256"], "lines": len(f["lines"]),
                           "symbols": len(f["symbols"])} for f in records]}
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    out = ["# File and symbol directory", "", "Snapshot: 22 October 2026. See [the written guide](README.md) first.", "",
           "Every function/class/type in the selected files is indexed below. Application notes are separately authored; source docstrings are copied as documentation and can be stale. Full per-line syntax annotations are in [the source companion](walkthrough.html).", ""]
    for index, f in enumerate(records):
        out += [f"## {index+1}. {f['path']}", "", f["purpose"], "",
                f"{len(f['lines'])} lines. [Open annotated source](walkthrough.html#file={index}).", ""]
        for s in f["symbols"]:
            out += [f"### `{s['name']}` — L{s['start']}–L{s['end']}", "",
                    f"[Read source and line explanations](walkthrough.html#file={index}&line={s['start']}).", ""]
            if s.get("note"):
                out += [s["note"], ""]
            if s.get("doc"):
                out += ["Source documentation (check against implementation):", "",
                        *[("> " + line).rstrip() for line in s["doc"].splitlines()], ""]
            if not s.get("note") and not s.get("doc"):
                out += ["No separate semantic description is supplied for this symbol. Its signature and every source statement are explained in the companion; follow the callers to establish application intent.", ""]
    (HERE / "file-guide.md").write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    print(json.dumps({"files": len(records), "source_lines": manifest["total_lines"],
                      "symbols": sum(len(f["symbols"]) for f in records),
                      "html_bytes": (HERE / "walkthrough.html").stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
