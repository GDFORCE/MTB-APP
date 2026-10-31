# Fresh-chat prompt: discover the symbols needed to represent any protocol (approach first, code later)

Prepared 29 October 2026. **Current fresh-chat prompt.** It supersedes
[protocol-elements-fresh-chat-prompt-2026-10-29.md](protocol-elements-fresh-chat-prompt-2026-10-29.md)
(preserved). It is for a new chat with no knowledge of this project. It is not a Claude Code
implementation prompt and doesn't authorize any change to this repository.

**The point:** the output is a **list of symbols** (the building blocks needed to write any protocol
rule). It starts from a seed list. Every protocol analysed is searched for the symbols it uses, and
anything the list can't express becomes a **new symbol** that is added to the list. The list is done
when new protocols stop adding symbols.

## How to use

1. Open a new chat. Paste the prompt below. Fill in or delete the `[ ]` lines at the end.
2. Discuss the approach. Type `code` when you're happy with it.

## Prompt

````text
I need help designing a solution. Please DISCUSS THE APPROACH FIRST. Do not write code until I say "code".

MY GOAL
I want to discover the complete set of SYMBOLS (basic building blocks) needed to represent the schedule
rules and conditions of ANY clinical trial protocol. I start from a seed list (below). I will analyse
protocols in batches (first 10, later many more). For each protocol the program must:
1. find every rule about WHEN, WHETHER or FOR WHOM a visit, dose or assessment happens;
2. write each rule using only the symbols in the current list (this proves which symbols the protocol uses);
3. when a rule cannot be written with the current symbols, propose a NEW SYMBOL;
4. add accepted new symbols to the list, so the next protocol is analysed with the bigger list.
The list is complete when a run of new protocols (for example 5–10 in a row, from different therapy areas
and designs) adds no new symbol. The final symbol list is the main result.

HOW A RULE IS WRITTEN
Every rule = WHEN <trigger> IF <condition> THEN <actions> (ELSE / TIMEOUT <actions>), built from symbols.
Example sentence: "If ANC < 1.0 × 10^9/L on Day 1 of a cycle, delay treatment until ANC ≥ 1.0. If recovery
takes more than 14 days, discontinue Drug X."
  WHEN  DUE(Drug X dose, Day 1 of each cycle)
  IF    COMPARE( AS_OF(LATEST(ANC), Day 1), <, 1.0 10^9/L )
  THEN  HOLD_UNTIL( Drug X dose,
          until:    COMPARE(LATEST(ANC), >=, 1.0 10^9/L),
          deadline: WITHIN(14 days, from planned Day 1),
          timeout:  STOP(Drug X dosing) )
        SET(this cycle's Day 1 = date condition met)
  NOT_STATED: whether later cycles shift
Symbols used: DUE, AS_OF, LATEST, COMPARE, HOLD_UNTIL, WITHIN, STOP, SET, NOT_STATED.

SEED SYMBOL LIST (version 1 — expected to grow)
Things:     ACTIVITY (visit, dose, assessment, contact, questionnaire, sample) · SERIES / OCCURRENCE (nth, first,
            last) · TRACK (screening, treatment, follow-up, extension) · GROUP (arm, cohort, sequence) · MEASURE (a
            recorded value with a unit) · EVENT (something that happens on a date) · TIME_POINT (a named date)
Time:       OFFSET (amount + unit, before/after) · WINDOW (allowed range) · DURATION · EVERY (interval, count, until,
            max time; from previous actual or fixed grid) · STAGES (interval changes over time) · WITHIN (deadline) ·
            EARLIEST_OF / LATEST_OF (whichever first / last)
Reading a value: AS_OF (value at a time) · LATEST / WORST / BEST · BASELINE / CHANGE_FROM · COUNT · CONSECUTIVE ·
            PERSISTS_FOR · CONFIRMED_BY
Comparison: = ≠ < ≤ > ≥ · BETWEEN · IN · TIMES_REFERENCE (e.g. × ULN)
Logic:      IF / ELSE · AND / OR / NOT · HAPPENED (event occurred by a date) · STATUS (activity done / missed / not done)
Actions:    SCHEDULE · SKIP · STOP · SHIFT / DELAY · HOLD_UNTIL (with deadline and timeout) · RESUME · REPEAT · CHANGE
            (dose level, frequency, modality) · SWITCH (group) · START (track) · SET (record a date or value) · ASK (a
            person decides) · WARN
Rule and evidence: WHEN · APPLIES_TO · PRIORITY (which rule wins) · SOURCE (page + quote) · NOT_STATED · UNCLEAR

WHAT COUNTS AS A NEW SYMBOL
- A rule that cannot be written with the current list, or can be written only by bending a symbol's meaning.
- NOT a new symbol: a new value (a new lab test, a new drug, a new unit) — those are values inside existing
  symbols (MEASURE "eGFR" is not a new symbol).
- NOT a new symbol: a combination of existing symbols. Recurring combinations are reported separately as
  "shortcuts" (they may become convenience symbols later, but they are not missing building blocks).
- Before proposing one, check that no existing symbol (including symbols added earlier in this run) already
  means the same thing under another name.

INPUT
- Protocol PDFs, 80–300 pages, any therapy area and design (oncology cycles, vaccines, crossover,
  bioequivalence, device, paediatric, observational, rare disease). Key content is in schedule-of-activities
  tables (multi-page, merged cells, tick marks) and their FOOTNOTES, plus dose modification, discontinuation
  and follow-up sections. Some are scanned or have cropped pages.
- Ignore: eligibility criteria, safety-reporting deadlines, data entry, shipping, statistics, and plain
  "assessment X at visit Y" table rows unless the WHEN or IF is unusual.

REQUIREMENTS
- Works for any protocol; no protocol-specific code.
- Never guess: a value the protocol doesn't give is "not_stated".
- Every rule and every new symbol carries a page number and a word-for-word quote; the program checks the
  quote is really in the PDF.
- Re-runs don't redo finished protocols. The symbol list is versioned: each protocol's result records which
  list version it was analysed with.

OUTPUT (fixed JSON keys)

1) symbols.json — the growing symbol list (the main result)
{
  "version": 3,
  "symbols": [
    { "name": "HOLD_UNTIL", "group": "action",
      "meaning": "Wait before an activity until a condition holds, with an optional deadline and timeout action.",
      "parameters": ["activity", "until (condition)", "deadline (WITHIN)", "timeout (actions)"],
      "origin": "seed" | "<protocol file where first found>",
      "added_in_version": 1,
      "used_in_protocols": 4,               (program)
      "used_in_rules": 9,                   (program)
      "example": { "file": "...", "page": 24, "quote": "..." } }
  ],
  "history": [ { "version": 2, "after_protocol": "P03.pdf", "added": ["CALENDAR_ALIGN"] } ]
}

2) <protocol>.json — one per protocol
{
  "protocol": { "file": "...", "sha256": "(program)", "title": "...", "phase": "...", "therapy_area": "...",
                "design": "...", "pages": 112, "pages_not_read": [ { "page": 97, "reason": "scanned" } ] },
  "symbol_list_version": 2,
  "rules": [ {
      "id": "R2", "page": 24, "location": "Table 5, footnote b", "quote": "If ANC is < 1.0 ...",
      "expression": "WHEN DUE(...) IF COMPARE(AS_OF(LATEST(ANC), Day 1), <, 1.0) THEN HOLD_UNTIL(...)",
      "symbols": ["DUE", "AS_OF", "LATEST", "COMPARE", "HOLD_UNTIL", "WITHIN", "STOP", "SET"],
      "fully_expressed": true,
      "not_stated": ["whether later cycles shift"],
      "quote_found": true (program) } ],
  "symbols_used": [ { "name": "HOLD_UNTIL", "rules": ["R2", "R7"] } ],          (program, from rules)
  "new_symbols": [ {
      "proposed_name": "CALENDAR_ALIGN", "group": "time",
      "meaning": "Place an activity on a calendar position (a weekday, a day of the month, a season), not an offset.",
      "parameters": ["calendar unit", "position"],
      "why_existing_cannot": "OFFSET and EVERY count days from a time point; none can say 'first Monday of each month'.",
      "rule": "R5", "page": 31, "quote": "Visits take place on the first Monday of each month.",
      "decision": "added" | "merged_into:<existing name>" | "rejected:<reason>" } ],
  "shortcuts": [ { "symbols": ["HOLD_UNTIL", "WITHIN", "STOP"], "seen_in_rules": ["R2", "R9"],
                   "suggested_name": "HOLD_WITH_DEADLINE" } ],
  "unclear": [ { "page": 24, "issue": "Table 5 says 14 days, Section 7.3 says 2 weeks or more" } ]
}

3) saturation.json — is the list still growing? (program)
[ { "order": 1, "file": "P01.pdf", "rules": 18, "fully_expressed": 16, "new_symbols_added": 2, "list_size": 47 },
  { "order": 2, "file": "P02.pdf", "rules": 25, "fully_expressed": 25, "new_symbols_added": 0, "list_size": 47 } ]

WHAT I WANT FROM YOU NOW (the discussion)
1. Ask the few questions you need answered first.
2. Review the seed list: anything missing, duplicated or badly named? Keep changes small and explain each.
3. How should new symbols be decided: accepted automatically, or reviewed by me after each protocol or batch?
   How do we stop the list filling with duplicates (same idea, different name) or with values disguised as
   symbols? Should a protocol analysed with an older list version be re-checked when the list grows?
4. Propose 2–3 overall approaches (for example: one AI pass per protocol; separate passes for finding rules
   and for writing them in symbols; PDF table/footnote extraction by code plus AI). For each: steps,
   strengths, weaknesses, rough cost and time for 10 protocols, likely accuracy. Recommend one.
5. How we check correctness: quote checks, schema validation, my spot checks, two runs or two models on the
   same protocol, a few protocols labelled by hand.
6. Risks: long PDFs, tables and footnotes, scanned pages, invented content, the order of protocols affecting
   which names win.

Keep it plain and practical. When I say "code", build it step by step with tests, run it on 2–3 protocols
first, and show me the new symbols found before running the rest.

What I can tell you already (fill in or delete):
[ ] Language / environment I prefer: ...
[ ] AI model or API I can use (and any budget limit): ...
[ ] Where the PDFs are and roughly how many in total: ...
````
