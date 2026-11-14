# Prompt: where protocol meaning is lost before the schedule

Saved 6 November 2026. Paste the block below into a fresh Claude Code session in this repo. It needs no earlier conversation.

```text
Task: find and fix where protocol meaning is lost between the AI reading a protocol and the patient schedule.

Background (assume you know nothing else):
This repo (MTB-APP) turns a clinical-trial protocol PDF into patient visit schedules:
1. backend/app/kit/pdf_input.py reads the PDF (text, schedule tables, symbols).
2. An AI reads the whole protocol and writes one JSON "kit model" in a fixed vocabulary: anchors
   (key dates), visits, repeats, procedures, events, rules, checks, measures, doses. The vocabulary is
   the schema in backend/app/kit/_prompt_text.py (do not edit) extended in backend/app/kit/prompts.py.
3. backend/app/kit/normalize.py cleans the model.
4. Two more AI passes: a table cross-check and an events check (backend/app/kit/events.py).
   backend/app/kit/analyze.py runs the whole pipeline.
5. After human review the model is saved, and frontend/src/features/kit/schedule.ts computes every
   patient's dates from the model alone.

The problem:
Only what fits the vocabulary changes dates. When the protocol says something the vocabulary cannot
hold, the AI either forces it into the nearest field (a wrong schedule) or writes free text (notes,
condition, effect) that the engine ignores. normalize.py then removes or changes more, often silently:
- a rule whose trigger is not a known anchor or event is dropped with no note (norm_rules);
- unknown visit ids are removed from stop_visits with no note;
- a visit with an unknown anchor is moved to the start anchor (normalize);
- an unknown anchor kind becomes "relative", an unknown modality becomes "site";
- a missing window becomes 0 days, the same as "no window allowed";
- extra fields the AI invents are stored but never used.
Known vocabulary gaps are the rows marked Missing or Partly in docs/kit-schema-element-inventory.md.
So when a schedule is wrong, nobody can tell whether the AI misread the protocol, the vocabulary could
not express it, or clean-up dropped it. This has never been measured on a real protocol.

Do all three steps:
1. Measure. docs/protocol-benchmarks/bp11-301-v2.1/ holds an independent expected answer for protocol
   BP11-301 (expected-behavior.md, oracle-draft.json; the PDF's path and hash are in
   source-manifest.json). Its comparison with the app was never run. Run the kit analysis on that PDF,
   compute the schedule, compare it with the expected answer, and put every mismatch in one class:
   misread / could not express / dropped or changed by clean-up / calculated wrong. Report counts and
   an example of each.
2. Let the AI say "I understood this but cannot express it": add an optional top-level list to the
   schema extension in prompts.py (for example "unrepresented": quote, page, affected visit/anchor
   ids, reason), keep it through normalize.py, and show it to the reviewer in the Add Trial schedule
   step. Use step 1's findings to decide what it must capture.
3. Make clean-up visible: every drop or default in normalize.py adds a note saying what changed and
   why. A value the protocol does not state stays unknown, never 0 or a guessed default.

Rules:
- Read AGENTS.md first and follow it. Models saved before this change must still load and give the
  same dates.
- Tests: run backend tests/test_kit_*.py files one at a time (never plain pytest over the suite);
  frontend `npm run test:kit` plus the kit tests in frontend/tests. Typecheck only files you touch.
- Step 1 needs OPENAI_API_KEY and the PDF. If either is missing, say so and continue with steps 2-3;
  never invent results.
- Do not commit unless asked. End with: the step 1 counts, what changed, and tests run.
```
