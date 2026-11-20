# Protocol workflow implementation and verification

## Application flow

1. In **Add trial**, upload a PDF or UTF-8 text protocol. The existing kit extractor reads the source and returns draft blocks.
2. Use **Workflow**, **Visits and activities**, and **Sample calendar** to inspect the draft. Open a block to view its references, inputs, outcomes, and source. Edit its operation to correct a value or path. Enter confirmed interpretations beside the affected blocks.
3. Save a draft to resume review or approve the current preview. Approval requires the original source file, the exact preview digest, valid block references, and the required review answers and interpretations. Unsupported operations remain approval blockers.
4. In **Add patient**, choose the trial and enter the starting date for its approved version. The calendar computes the visits the available inputs support. Staff can record bookings, actual visit dates, answers, and events in the patient calendar. Booking an existing visit leaves the target calculation intact.
5. To amend a workflow, open **Protocol workflow** from the trial summary and upload the revised document. Compare blocks before approval. Each patient stays on their assigned version. In the patient calendar, **Preview changes** lists changed open visits before staff explicitly move the patient.

The approved artifact stores the source hash, settings, and executable operations. The server checks the artifact digest and compiles the blocks into the input for the same kit engine used by the sample and patient calendars. A shared study date is a PI input for an approved version and can populate its dependent visits without issuing a new workflow version.

## Verification evidence

The route tests use in-memory storage and replay extraction. They exercise the application upload, review, draft, approval, invitation, patient calendar, and migration routes with the same TypeScript engine the app calls. The test materializes the invitation record in memory. It bypasses OTP and external messaging.

| Check | Evidence |
| --- | --- |
| Benzoyl fixed dates and windows | The timing fixture uses physical PDF pages 46–48. An actual Day 0 of January 1, 2026 yields January 29, February 26, and March 12, each with a four day window on both sides. The same approved workflow produces a different calendar across leap day for a second participant. A later interim booking and actual leave the two later targets unchanged. |
| CRD/09 confirmed booking and elapsed time | The timing fixture uses physical PDF pages 39 and 42–43. The second period stays pending, rejects a booking 20 days after the first dose, and accepts a booking at 21 days. Each period places its example 24 hour sample on the correct following day and keeps its own dose clock. |
| Review and version adoption | An unresolved issue prevents approval. A saved draft can be reopened. An invalid edited reference is rejected. Approval retains the original upload. The amendment fixture changes a future visit, leaves the existing patient pinned, and preserves the actual date and its original version after explicit adoption. |
| Shared date and permissions | The shared study date generates the dependent visit while leaving the fixed visits unchanged. A CRC cannot set it. An identical retry leaves the original revision and audit count intact. Investigator only question answers are enforced by the server. |
| Record updates | Two identical updates produce one revision and one audit entry. Two identical event records with the same key produce one event. A corrected branch leaves already recorded attendance visible. |

Run the workflow evidence from `backend/` with `python -m pytest -q tests/test_kit_workflows.py`. The existing kit engine regression suite runs from `frontend/` with `npm run test:kit`.

## Remaining acceptance work

- Attempted live extraction of the full Benzoyl PDF. The model provider returned a quota error during the final analysis pass. Full document extraction and the named protocol corpus have no end to end pass through this app flow. The route replay covers reviewed timing subsets for Benzoyl and CRD/09 and synthetic workflow changes. The eleven protocol CBS results come from a separate prototype.
- The current workflow executes the operations supported by the kit engine. History-based intervals and confirmed study-wide events (including the P16 final-participant closeout) were added later the same day: see [kit-adaptive-intervals-study-events-2026-11-20.md](kit-adaptive-intervals-study-events-2026-11-20.md). Automatic detection of a study-wide event, separate cohort or site state, general event history expressions, and an isolated custom Python runner still need implementation.
- The client still calculates the calendar. Updates audit the actor, revisioned facts, and input categories changed. Server generated differences, kit visit reminders, per assessment completion history, and a server projected calendar for information requiring blinding remain open. An amendment preserves recorded visit dates and their source version, with adoption blocked when a recorded visit key is incompatible with the new model.
- Browser and mobile acceptance, real registration, GridFS behavior against a live database, full source coverage, and independent clinical review of each named protocol remain separate acceptance checks.
