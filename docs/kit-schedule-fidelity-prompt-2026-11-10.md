# Prompt: any protocol in, correct patient schedule out

Saved 10 November 2026. Paste the block below into a fresh Claude Code session (Fable 5.2 recommended). It needs no earlier conversation. It states the goal, our approach and our problem, and leaves the solution to the session. It supersedes [kit-representation-gap-prompt-2026-11-06.md](kit-representation-gap-prompt-2026-11-06.md) (kept).

```text
Read AGENTS.md first and follow it. Then solve the problem below. How you solve it is your call:
investigate, decide, and change the design if that is what it takes.

GOAL
When a user uploads any clinical-trial protocol PDF, the app must form the correct schedule for that
trial and for every patient in it, with no one hand-building anything per protocol. The schedule means
everything the protocol requires at each step: which visits happen and when (with windows), what is done
at each visit, which things depend on conditions (arm, cohort, a result, an earlier visit, a dose level),
and how the plan changes when something happens (an event, a missed visit, a stop, a dose change).
Protocols are written by different sponsors in different ways. We cannot predict their structure, so the
solution must not depend on guessing it in advance.

OUR APPROACH TODAY (kit pipeline, backend/app/kit and frontend/src/features/kit)
1. pdf_input.py reads the PDF (text, schedule tables, symbols).
2. An AI (OpenAI; client in llm.py) reads the protocol and writes one JSON "kit model" in a fixed
   vocabulary: anchors, visits, repeats, procedures, events, rules, checks, measures, doses
   (backend/app/kit/_prompt_text.py, extended in prompts.py; do not edit _prompt_text.py).
3. normalize.py cleans the model. Two more AI passes cross-check tables and events (analyze.py, events.py).
4. A person reviews it in Add Trial. The model is saved.
5. frontend/src/features/kit/schedule.ts computes every patient's dates from the saved model alone.
Docs: docs/kit-schema-element-inventory.md lists what the vocabulary can and cannot express.

OUR PROBLEM
For about a month, uploading a real protocol has not given an accurate schedule. The conditions and the
"what to do at each step" details come out wrong or missing, and the failures differ per protocol. We
suspect the cause is structural: only what fits the fixed vocabulary can change a date; what does not fit is
forced into the nearest field (wrong but plausible), written as text the engine ignores, or removed or
altered by normalize.py, often silently. So when a schedule is wrong we cannot tell whether the AI misread
the protocol, the vocabulary could not express it, clean-up dropped it, or the engine calculated it wrong.
Adding one more field per protocol has not converged. Treat this diagnosis as a hypothesis and check it.

OUTPUT WE NEED
Upload a protocol, get a schedule that matches what the protocol says, for protocols we have never seen.
Where the app cannot be sure or cannot represent something, it must say so to the reviewer and show the
protocol quote and page, never invent a value and never drop it quietly. A protocol that works today must
keep giving the same schedule.

HOW TO KNOW IT WORKS
docs/protocol-benchmarks/bp11-301-v2.1/ has an independent expected answer for a real protocol (the PDF
path and hash are in source-manifest.json; the app was never scored against it; the expected answer is an
AI-written draft, so treat its listed open questions as open, not as app errors). Use it, plus the sample
models and tests already in the repo, to measure before and after. Ask me for other real protocol PDFs if
you need more cases; do not download any.

Also build a synthetic test bench, because real protocols are few. Invent complex, realistic hypothetical
protocols (as PDFs written in different sponsor styles: tables, prose-only, footnotes, merged cells,
Day 1 vs Day 0, cycles, crossover, extension, event-triggered visits, dose reductions, lab-gated visits,
early termination, windows, missed visits) that together cover every kind of rule you can think of, and
write the expected schedule for each by hand from the rules, independently of the app's output (derive it
from the invented rules, not from running the app). Keep them clearly marked as constructed fixtures, never
as real protocols. Use them at two levels: (1) protocol PDF -> whole pipeline -> compare with the expected
schedule, to find where meaning is lost; (2) hand-written models -> engine only, no AI, to check the
calculation. Add the cases the benchmark and your fixtures expose as regression tests. Be honest about the
limit: fixtures only cover what we think of, so report which real-protocol behaviours they do not cover.

TIME AND PERSISTENCE
There is no deadline; take as long as it needs. Do not stop at a partial improvement, a plan, or a report of
what could be done. Work in loops: measure, find the biggest cause of wrong schedules, fix it, re-measure on
the benchmark and the synthetic bench, repeat until the schedules match the expected answers (or the
remaining gaps are only things the app now reports to the reviewer instead of losing). Do not pause to ask
for approval between phases; ask only when you are blocked (a missing PDF or API key, a decision that
changes the extraction provider, or spending beyond what you need). Redesign if the evidence says the
current design cannot get there. Keep a short running log in docs/ (what you measured, changed and
learned) so a later session can continue, and finish only when you can show the before/after results.

CONSTRAINTS
- AGENTS.md rules apply (no guessing unstated values, kit-only scheduling, testing recipe, no commits
  unless asked). Backend tests: run tests/test_kit_*.py one file at a time. Frontend: npm run test:kit.
  Typecheck only files you touch.
- Keep the existing sample goldens passing unless a change is deliberate and explained.
- Do not change the extraction provider/model or spend model credits beyond what you need without asking.
  OPENAI_API_KEY and the BP11-301 PDF may be missing: say so and continue with what you can test.
- C: is nearly full; keep outputs on D: or in the repo.
- If a prompt or doc is useful for follow-up work, save it under docs/ and update
  docs/claude-code-prompts-index.md.

FINISH WITH
What you found (where schedules are actually wrong, with counts if you could measure), what you changed
and why, what the app now does with something it cannot represent, tests run, and what is still open.
```
