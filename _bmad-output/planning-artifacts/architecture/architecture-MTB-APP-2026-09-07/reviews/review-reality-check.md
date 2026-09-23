# Reality-Check Review — ARCHITECTURE-SPINE.md (MTB Scheduling Subsystem)

- **Reviewed file:** `d:/MTB_Intern_work_folder/APP-New-approch/MTB-APP/_bmad-output/planning-artifacts/architecture/architecture-MTB-APP-2026-09-07/ARCHITECTURE-SPINE.md`
- **Review date:** 2026-09-08
- **Lens:** Was every committed decision reality-checked against manifests, the web, and the code — or asserted from training data?
- **Evidence base:** `backend/requirements.txt`, `frontend/package.json`, `README.md`, `backend/.env.example`, live PyPI/npm/vendor data, and direct reads of `backend/app/**` and `frontend/**`.

---

## Verdict

**The Stack table is clean — every single version was copied from a real manifest, not invented.** That is the strongest signal in the document: 16/16 rows verified, zero fabrications, zero drift. The failure is on the *other two* axes. Currency was never checked at all (one dependency is past its vendor end-of-life *as of today*; two unbounded pins already admit a breaking major), and several Structural Seed / Capability Map statements describe a target state in the present tense, so a reader cannot tell which paths exist today and which are being commissioned.

---

## 1. Stack table vs. actual manifests

Every row cross-checked against `backend/requirements.txt`, `frontend/package.json`, and `README.md`.

| Spine row | Spine value | Manifest value | Source | Verdict |
| --- | --- | --- | --- | --- |
| Python | `3.11+` | `Node.js 20+ / Python 3.11+` | `README.md` Prerequisites | MATCH |
| FastAPI | `0.110.1` | `fastapi==0.110.1` | requirements.txt:1 | MATCH (pin fidelity correct) |
| uvicorn | `0.25.0` | `uvicorn==0.25.0` | requirements.txt:2 | MATCH |
| pydantic | `>=2.6.4` | `pydantic>=2.6.4` | requirements.txt | MATCH |
| SQLAlchemy | `>=2.0,<2.1` | `SQLAlchemy>=2.0,<2.1` | requirements.txt | MATCH |
| alembic | `>=1.13,<2` | `alembic>=1.13,<2` | requirements.txt | MATCH |
| psycopg (binary) | `>=3.2,<4` | `psycopg[binary]>=3.2,<4` | requirements.txt | MATCH |
| pymongo / motor | `4.6.3 / 3.3.1` | `pymongo==4.6.3`, `motor==3.3.1` | requirements.txt | MATCH (see F-1) |
| langgraph | `>=1.1.6,<1.2` | `langgraph>=1.1.6,<1.2` | requirements.txt | MATCH (see F-4) |
| anthropic | `>=0.69.0` | `anthropic>=0.69.0` | requirements.txt | MATCH (see F-2) |
| google-genai | `>=2.17.0` | `google-genai>=2.17.0` | requirements.txt | MATCH |
| pypdfium2 | `>=4.30.0` | `pypdfium2>=4.30.0` | requirements.txt | MATCH (see F-2, F-7) |
| Node.js | `20+` | `Node.js 20+` | `README.md` | MATCH |
| Expo / expo-router | `~54.0.36 / ~6.0.22` | `"expo": "~54.0.36"`, `"expo-router": "~6.0.22"` | package.json | MATCH (see F-3) |
| React / React Native | `19.1.0 / 0.81.5` | `"react": "19.1.0"`, `"react-native": "0.81.5"` | package.json | MATCH |
| TypeScript | `~5.9.3` | `"typescript": "~5.9.3"` (devDependencies) | package.json | MATCH |

**No invented versions. No loosened pins. No tightened pins.** Constraint operators (`==` vs `>=` vs `~`) were preserved faithfully, including the SQLAlchemy and alembic upper bounds and the Expo caret-tilde ranges. This part of the document was genuinely read off the repository.

**Omissions worth noting** (not errors, but they leave the envelope incomplete — see F-5, F-6): `emergentintegrations==0.2.0`, `ollama>=0.6.0`, `reportlab==4.4.3`, `openpyxl==3.1.5`, `pandas`, `numpy`. The first two matter because the README documents Ollama as a first-class extraction provider and `emergentintegrations` is a hard `==` pin that does not resolve on public PyPI.

---

## 2. Currency / maintenance check (web-verified)

| Package | Pinned | Current (Sep 2026) | Gap | Assessment |
| --- | --- | --- | --- | --- |
| **motor** | `3.3.1` | 3.7.x — **deprecated 2025-05-14, EOL 2026-05-14** | past EOL | **CRITICAL — see F-1** |
| **anthropic** | `>=0.69.0` (no ceiling) | **1.4.0** | major boundary open | **HIGH — see F-2** |
| **pypdfium2** | `>=4.30.0` (no ceiling) | **5.13.0** | major boundary open | **HIGH — see F-2** |
| Expo SDK | `~54.0.36` | **57.0.20** (SDK 56 on 2026-05-21) | 3 majors | MEDIUM — see F-3 |
| React Native | `0.81.5` | **0.86** (via SDK 57) | ~5 minors | MEDIUM — see F-3 |
| React | `19.1.0` | 19.2 | 1 minor | LOW |
| FastAPI | `0.110.1` | **0.141.1+** | ~31 minors (~2 yr) | MEDIUM — see F-4 |
| uvicorn | `0.25.0` | **0.52.3** (2026-08) | ~27 minors | MEDIUM — see F-4 |
| langgraph | `>=1.1.6,<1.2` | **1.2.x** (2026-05-21+) | ceiling excludes current minor | MEDIUM — see F-4 |
| pydantic | `>=2.6.4` | 2.13.5 (2026-08-28) | same major | OK |
| SQLAlchemy | `>=2.0,<2.1` | 2.0.52 — **no 2.1 exists** | none | OK — bound is correct and non-speculative |
| alembic | `>=1.13,<2` | 1.x | none | OK |
| psycopg | `>=3.2,<4` | 3.x | none | OK |
| google-genai | `>=2.17.0` | 2.22.0 | same major | OK |
| TypeScript | `~5.9.3` | 5.9.x current line | none | OK |
| emergentintegrations | `==0.2.0` | **404 on PyPI** | unresolvable | **HIGH — see F-5** |

All named technologies still exist and are maintained, **with the single exception of motor**.

---

## 3. Codebase claims vs. actual code

### Verified present (no action)

| Spine claim | Reality |
| --- | --- |
| `backend/app/extraction/` | exists |
| `backend/app/domain/schedule/` | exists — 13 modules |
| `backend/app/services/` | exists |
| `backend/app/api/uctsm.py` | exists |
| `backend/server.py` | exists |
| `app/services/schedule_projection.py`, `operational_read.py` | both exist |
| `frontend/src/features/uctsm/` | exists (11 files) |
| `frontend/app/(app)/` | exists |
| `canonical_import.py`, `canonical_bridge.py` (AD-2) | both exist in `app/services/`; `CanonicalSchedulePlan` referenced in both |
| `_render_pdf_pages` (AD-2a) | exists — `backend/protocol_extraction.py:1814` |
| `Trial.schedule_read_mode` (AD-1) | `app/db/models.py:56`, default `"LEGACY"` |
| `UCTSM_AUTHORITATIVE` (AD-1) | read at `server.py:5698, 6061, 6217, 10383` |
| `ParityRun`, `ParityService.readiness` (AD-1a) | `app/db/models.py:60`; `app/services/parity_service.py:40,139` |
| `timing.py`, `anchor_status.py`, `evaluator.py` (ENG-1) | all exist |
| `conditional.py`, `condition.py`, `impact_service.py` (ENG-2) | all exist |
| `RepeatBlock` / `RollingHorizon` (ENG-3) | `models.py:359`, `models.py:634` |
| `DependencyMode` (ENG-4) | `models.py:54` |
| `evaluator.evaluate_activities` (ENG-5) | `evaluator.py:650` |
| `ConfinementDefinition/DayDefinition/EpisodeDefinition`, `validator.py` (ENG-6) | `models.py:335,370,378` |
| `Qualifier`, `QualifierScope`, `QualifierCategory`, `extraction/completeness.py` (ENG-7) | `models.py:307,61,77`; file exists |
| `enrolment.py`, `uctsm_event_applicability` (ENG-8) | file exists; `app/db/models.py:253` |
| `uctsm_schedule_versions`, `diff.py` (ENG-9) | `app/db/models.py:139`; file exists |
| `Event.event_type` / `visit_mode` (ENG-10) | `models.py:445`, `models.py:457` |
| `ScheduleStatus`, `ConfinementStatus` enums (AD-6) | `models.py:24`, `models.py:137` |
| `TemporalAmount` (Conventions) | exists in `app/domain/schedule/timing.py` (not `models.py`; spine gives no path, so not an error) |
| `AuditEvent` (Conventions) | `app/db/models.py:587`, table `uctsm_audit_events` |
| "No domain module imports a service, a session, or a provider" | **CONFIRMED** — grep for service/SQLAlchemy/Session imports across `app/domain/schedule/*.py` returns nothing. This invariant genuinely holds today. |
| "SQLite at `backend/uctsm-dev.db` in development only" | file exists; `backend/.env.example:105` sets `UCTSM_DATABASE_URL=postgresql+psycopg://...` — dual claim confirmed |
| "No CI configuration exists in the repository" (Deferred) | **CONFIRMED** — no `.github/` directory. Correctly reality-checked. |

### Discrepancies

See F-6 through F-9 below.

---

## Findings

### F-1 — CRITICAL — `motor==3.3.1` is past vendor end-of-life and the spine records it with no note

- **Where:** Stack table row `pymongo / motor | 4.6.3 / 3.3.1`; underpins AD-1's "`visit_instances` remains the permanent system of record".
- **What is wrong:** MongoDB deprecated Motor on 2025-05-14 in favour of the PyMongo Async API. **End of life was 2026-05-14 — four months before this document's stated date of 2026-09-07.** Only critical bug fixes are provided until 2027-05-14, after which support ends entirely. The pinned `3.3.1` is also several minors behind the terminal `3.7.x` line.
- **Why it matters here specifically:** AD-1 makes an explicit, load-bearing commitment that the Mongo operational store is **permanent** — "`visit_instances` remains the permanent system of record for operational workflow state... and the engine never acquires equivalents." A permanent store reached through a driver that is already out of support is an unfunded liability written into the architecture's most durable decision. A Motor → PyMongo Async migration is a real, non-trivial piece of work (`AsyncIOMotorClient` → `AsyncMongoClient`, cursor and GridFS API changes) that AD-1 currently makes invisible.
- **Fix:** Add a driver-migration item — either as a new AD or in Deferred with a trigger date — stating that the permanent operational store migrates from Motor to PyMongo Async, and annotate the Stack row `motor 3.3.1 (deprecated; EOL 2026-05-14 — migrating to PyMongo Async)`.

### F-2 — HIGH — Two unbounded pins already admit a breaking major, and the spine presents them as the versions in use

- **Where:** Stack table rows `anthropic | >=0.69.0` and `pypdfium2 | >=4.30.0`.
- **What is wrong:** Both are transcribed faithfully from `requirements.txt`, but both have **no upper bound and a newer major already on PyPI**:
  - `anthropic` is at **1.4.0**. `>=0.69.0` resolves to 1.x. The 0.x → 1.0 transition carried breaking changes; `app/extraction/llm_client.py` (`AnthropicClient`) and `app/extraction/claude_provider.py` are the code that would break, and they sit on the Compile path AD-2 declares the *only* extraction pipeline.
  - `pypdfium2` is at **5.13.0**. `>=4.30.0` resolves to 5.x across a major boundary.
- **Why it matters:** A clean `pip install -r requirements.txt` today does not reproduce what the spine describes. The document says "≥0.69.0" in a table titled *Stack*, which a reader takes as "we are on 0.69-ish"; the build actually installs 1.4.0. This is the reverse of the failure mode AD-5 exists to prevent — an unstated bound silently resolving to something nobody chose.
- **Fix:** Either ceiling both pins in `requirements.txt` (`anthropic>=0.69,<2`, `pypdfium2>=4.30,<6`) and update the table to match, or annotate the rows with the major actually resolved. The document should not report a floor as if it were a version.

### F-3 — MEDIUM — The frontend stack is three Expo majors behind, recorded without a currency or support note

- **Where:** Stack rows `Expo / expo-router ~54.0.36 / ~6.0.22`, `React / React Native 19.1.0 / 0.81.5`.
- **What is wrong:** Versions match `package.json` exactly. But Expo SDK **57.0.20** is current (SDK 56 shipped 2026-05-21 with RN 0.85; SDK 57 carries RN 0.86). SDK 54 is three majors behind and at/near the edge of Expo's rolling support window; React Native 0.81.5 trails 0.86 by roughly five minors.
- **Why it matters:** The spine is a build substrate for work that will run for months. An Expo upgrade of that size is a schedule item that touches `frontend/src/features/uctsm/` and every `app/(app)/` route, and it is currently invisible in both the Stack table and Deferred.
- **Fix:** Add an Expo/RN upgrade to Deferred with a revisit trigger, or state explicitly that SDK 54 is pinned for the duration and why.

### F-4 — MEDIUM — Three backend pins are materially behind, and the langgraph ceiling now excludes the current minor

- **Where:** Stack rows `FastAPI 0.110.1`, `uvicorn 0.25.0`, `langgraph >=1.1.6,<1.2`.
- **What is wrong:**
  - FastAPI `0.110.1` (April 2024) vs **0.141.1+** — roughly 31 minor releases and two years behind.
  - uvicorn `0.25.0` vs **0.52.3** (2026-08).
  - langgraph `>=1.1.6,<1.2` — LangGraph **1.2** shipped 2026-05-21 and is the current line; the `<1.2` ceiling now pins the project one minor below current. `app/extraction/graph.py` imports `StateGraph`, `START`, `END` from it, so this is the extraction pipeline's core dependency.
- **Why it matters:** These are hard `==` pins (FastAPI, uvicorn) that no one has revisited in two years, on the serving layer described in the Operational envelope. The langgraph ceiling is the sharper issue: it is a deliberate cap that is now stale, and the spine reproduces it without noting that 1.2 exists.
- **Fix:** Note the langgraph ceiling's rationale and expiry in the spine (or lift it to `<2`); add a dependency-refresh item to Deferred covering FastAPI/uvicorn.

### F-5 — HIGH — `emergentintegrations==0.2.0` is not resolvable from public PyPI and is absent from the Stack table

- **Where:** `backend/requirements.txt:31`; omitted from the spine's Stack table.
- **What is wrong:** `https://pypi.org/pypi/emergentintegrations/json` returns **404**. The package is not on public PyPI, yet it is a hard `==0.2.0` pin in the file the README instructs every developer to run (`pip install -r requirements.txt`). A clean environment following the README fails at install.
- **Why it matters:** The spine's Operational envelope asserts a FastAPI/uvicorn backend serving both stores as though the stack is reproducible. It is not reproducible from the manifest as written, and the spine gives no signal of that. This is exactly the class of thing a reality-check pass is supposed to surface.
- **Fix:** Either remove or vendor the dependency, or document the private index in the README and record the constraint in the spine's Operational envelope.

### F-6 — HIGH — The Structural Seed describes `app/extraction/` in the present tense, but the ingestion port and image fallback AD-2a mandates do not exist

- **Where:** Structural Seed line `extraction/ # Compile: ingestion port, providers, claim graph, qualifier resolution`; AD-2a.
- **What is wrong:** Actual contents of `backend/app/extraction/`: `claude_provider.py`, `completeness.py`, `graph.py`, `llm_client.py`, `prompts.py`, `runner.py`. Concretely:
  - **No ingestion port module exists.** `grep -rln "pypdfium2\|render_pdf\|page_image" backend/app/ --include=*.py` returns **nothing** — no code under `app/` renders a page image at all.
  - `app/extraction/runner.py:36` `pages_from_pdf()` is **text-only** (delegating to `protocol_document_index._extract_pdf_text_pages`). AD-2a's rule that "a page whose text layer is empty MUST route to page-image" has **no implementation** in the new pipeline; a scanned protocol produces empty-text pages that flow straight through — the precise failure AD-2a says it prevents.
  - **"providers" is plural but there is one:** `claude_provider.py`. `grep -rln "gemini\|Gemini" backend/app/ --include=*.py` returns nothing. The Gemini and Ollama providers the README documents as first-class live only in the legacy root-level `protocol_extraction.py`.
- **Why it matters:** AD-2a reads as a description of an existing boundary ("Document ingestion **is** a port with two implementations behind one interface"), and the Structural Seed reinforces it by listing "ingestion port, providers" alongside directories that genuinely exist. A builder will assume the port is there to extend. It is not — the port, the second implementation, and the provider plurality are all still to be built.
- **Fix:** Mark the ingestion port and the non-Claude providers as *to be built* in the Structural Seed (e.g. `extraction/ # Compile: claim graph, qualifier resolution, Claude provider — ingestion port + image fallback TO BUILD`), and restate AD-2a's rule in the imperative rather than the descriptive present.

### F-7 — MEDIUM — `app/db/` is annotated "migrations", but Alembic lives at `backend/alembic/`

- **Where:** Structural Seed line `db/ # SQLAlchemy models, repositories, migrations`; Operational envelope "Alembic owns canonical schema migration".
- **What is wrong:** `backend/app/db/` contains exactly `base.py`, `models.py`, `repositories.py` — **no migrations**. Alembic is at `backend/alembic/` (`env.py`, `script.py.mako`, `versions/`) with `backend/alembic.ini`. The Structural Seed is the map a builder navigates by; a developer looking for a migration under `app/db/` will not find one.
- **Fix:** Correct to `db/ # SQLAlchemy models, repositories` and add `alembic/ # canonical schema migrations` as its own line under `backend/`.

### F-8 — MEDIUM — The dependency-direction graph omits a real `extraction → services` edge

- **Where:** the Invariants & Rules mermaid graph — it shows only `EXTRACT --> MODEL`.
- **What is wrong:** `backend/app/extraction/runner.py:22-23` imports `from app.db import models as db` **and** `from app.services.extraction_service import ExtractionService`. So Compile depends on both the services layer and the db layer, neither of which appears as an edge from `EXTRACT` in the graph the document introduces with "arrows mean *depends on*... that is the rule, not an accident."
- **Why it matters:** The graph's credibility rests on it being the real dependency picture. The one invariant it most emphasises — the canonical model as a leaf — **does hold** (verified: no domain module imports a service, session, or provider). But the missing extraction edges mean the graph is a partial drawing presented as complete, so a reader cannot tell which absent edges are forbidden and which are merely undrawn.
- **Fix:** Either add `EXTRACT --> OPS`/services edges, or state that the graph shows only the edges under invariant control and name the ones deliberately omitted.

### F-9 — LOW — Engine count is inconsistent across the document, and the "one engine per module" convention is contradicted by the spine's own map

- **Where:** frontmatter `binds:` (ten), paradigm diagram node `E[Eight ordered engines]`, AD-4e composition graph (seven nodes), AD-4a table (seven rows), Structural Seed comment "the ten engines".
- **What is wrong:**
  - The paradigm diagram says **eight**; the frontmatter binds **ten**; AD-4e orders **seven**. Dependency mode (ENG-4) and Version (ENG-9) are parameters/scopes rather than ordered engines per AD-4b and AD-4d, which explains 10 → 8, but nothing in the document reconciles 8 → 7.
  - The Conventions row asserts "One engine per module in `app/domain/schedule/`, named for the engine." The Capability Map itself contradicts this: Applicability lives in `enrolment.py`, Repeating in `models.py` + `evaluator.py`, Activity in `evaluator.evaluate_activities`, Confinement in `models.py` + `validator.py`, Event type in `models.py`. Only Conditional and (partly) Anchor have engine-named modules.
- **Fix:** Reconcile the counts with one sentence, and mark the one-engine-per-module convention as a target with the current exceptions named — otherwise the Capability Map reads as a set of convention violations rather than as today's honest layout.

---

## Asserted with confidence but unconfirmable from web, manifest, or code

Listed for transparency. These are not necessarily wrong — they are simply not checkable from the evidence available to this review, and the document does not flag them as unverified.

1. **All `doc N sNN` specification citations** (AD-6, AD-7, the Deferred items). Every reference to `MTB-app.pdf` — "doc 10 s3", "doc 7 s8/s9", "doc 9 s34", "doc 6 s36", "doc 1 s16", "doc 2 s32", "doc 9 s30–32" — is unverifiable here; the 272pp source was not available. These citations carry the load for AD-6's entire enum list and for two Deferred scope decisions ("the specification places it outside Phase 1"). They should be spot-checked against the PDF by someone with it open.
2. **AD-1a is written as an existing rule but is entirely prospective.** `ParityRun` (`app/db/models.py:60`) has columns `verdict, compared, matched, differences, note, ran_by, ran_at` — **no reviewer-decision field, no `ACCEPTED` state, no reason field**. `ParityVerdict` (`app/domain/schedule/parity.py:34-46`) has `MATCH` and `AMBIGUOUS_MATCH`, not `ACCEPTED`. `ParityService.readiness()` (`parity_service.py:139`) passes a patient only on `run.verdict != ParityVerdict.MATCH.value` — i.e. `MATCH` alone, not `MATCH ∨ ACCEPTED`. The decision is sound; the phrasing ("`ParityRun` **carries** a reviewer decision") states it as present fact. Restate in the imperative.
3. **AD-6's prescribed enum changes are not yet made.** `Event.event_type` is `str = Field(min_length=1)` at `models.py:445`, still a free string; `QualifierScope` has not split `BLOCK`. Again correct as a decision, but the "Binds: the enums in `app/domain/schedule/models.py`" phrasing invites a reader to think the taxonomy is closed today. It is not.
4. **"Extraction runs as a background worker off the request path"** (Operational envelope). `app/extraction/runner.py` is written to be called by a worker and records failure on the run, consistent with the claim — but no worker process, queue, or scheduler was located. Whether a worker is actually deployed is unconfirmed.
5. **"The projection is complete; no consumer reads it"** (Deferred). `schedule_projection.py`, `operational_projection.py`, `notification_projection.py`, and `reminder_delivery.py` all exist; "complete" is a quality judgement not verifiable from file presence.
6. **AD-8's provenance claim** — that *every* materialized row stores `schedule_version_id`, source definition id, and `protocol_version_id` — was not exhaustively verified column-by-column across patient events, patient activities, and confinement episodes.
7. **Single-worker WebSocket connection map** (Deferred). Plausible and correctly hedged, but the connection map was not located during this review.

---

## What this review confirms was done well

Worth stating plainly, because it is the part most likely to be assumed rather than checked:

- **The Stack table is honest.** 16/16 rows match a real manifest, with constraint operators preserved. Nothing was reconstructed from memory. This is the single most common failure mode for an architecture document and it did not happen here.
- **The "no CI exists" claim in Deferred is true** — verified, no `.github/`. The document declined to assume a CI setup it had not seen.
- **The canonical-model-as-leaf invariant genuinely holds in code** — no module in `app/domain/schedule/` imports a service, a session, or a provider. The most important structural claim in the document is real, not aspirational.
- **The SQLAlchemy `<2.1` bound is correct and non-speculative** — no 2.1 release exists; the ceiling is not a guess at a version that isn't there.
- **Almost every code symbol in the Capability Map resolves**, with exact line numbers, including non-obvious ones like `evaluate_activities` and the `uctsm_event_applicability` table name.

---

## Recommended actions, in order

1. **F-1** — Add the Motor→PyMongo Async migration to the document; annotate the Stack row. AD-1 currently commits to a permanent store on an out-of-support driver.
2. **F-5** — Resolve `emergentintegrations` (remove, vendor, or document the private index). The documented install is currently broken.
3. **F-2** — Ceiling `anthropic` and `pypdfium2`, or annotate the majors actually resolved.
4. **F-6** — Rewrite the `extraction/` Structural Seed line and AD-2a in the imperative; the ingestion port and image fallback do not exist.
5. **F-7** — Move `migrations` out of the `db/` annotation; add `alembic/`.
6. **F-8** — Add or explain the `extraction → services` edge.
7. **F-3, F-4** — Add a dependency-currency item to Deferred covering Expo/RN and FastAPI/uvicorn/langgraph.
8. **F-9** — Reconcile the engine count; mark one-engine-per-module as a target with named exceptions.
9. **Unconfirmable item 2 and 3** — Restate AD-1a and AD-6 in the imperative so no reader mistakes them for present behaviour.

---

### Sources

- [Motor — MongoDB Docs](https://www.mongodb.com/docs/drivers/motor/)
- [mongodb/motor — GitHub](https://github.com/mongodb/motor)
- [anthropic — PyPI](https://pypi.org/project/anthropic/)
- [pypdfium2 — PyPI](https://pypi.org/project/pypdfium2/)
- [google-genai — PyPI](https://pypi.org/project/google-genai/)
- [pydantic — PyPI](https://pypi.org/project/pydantic/)
- [SQLAlchemy — PyPI](https://pypi.org/project/SQLAlchemy/)
- [langgraph — PyPI](https://pypi.org/project/langgraph/)
- [LangGraph releases — GitHub](https://github.com/langchain-ai/langgraph/releases)
- [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/)
- [Uvicorn release notes](https://uvicorn.dev/release-notes/)
- [Expo SDK 57 changelog](https://expo.dev/changelog/sdk-57)
- [The React Native Ecosystem in Mid-2026 — Expo SDK 56, RN 0.85](https://medium.com/@dinesh.kachhot/the-react-native-ecosystem-in-mid-2026-expo-sdk-56-react-native-0-85-and-react-19-2-59919e4d76c5)
