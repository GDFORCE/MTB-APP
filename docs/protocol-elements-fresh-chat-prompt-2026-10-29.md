# Fresh-chat prompt: find the basic elements of clinical trial protocols (approach first, code later)

> **Superseded on 29 October 2026 by [protocol-symbols-discovery-fresh-chat-prompt-2026-10-29.md](protocol-symbols-discovery-fresh-chat-prompt-2026-10-29.md)**, which makes the growing symbol list the main result: a seed list, new symbols found per protocol, and a saturation count. Preserved for history.

Prepared 29 October 2026. **For a new chat with no knowledge of this project.** It describes the
problem from scratch and doesn't mention the kit model, the atom lists or earlier prompts, so the
new chat can think about it fresh. It fixes the output to one JSON format, so every protocol's
result can be compared and counted by code. It asks for an approach discussion first and code only
when you say so. It is not a Claude Code implementation prompt and doesn't authorize any change to
this repository.

Version 2 (29 October 2026): adds the fixed JSON format, its allowed values and a worked example.

## How to use

1. Open a new chat (Claude, ChatGPT, Gemini, any of them).
2. Paste the prompt below. Fill in the three `[ ]` lines at the end if you know the answers;
   otherwise delete them and the chat will ask.
3. Discuss the approach and the format. When you're happy with them, type `code`.

## Why a fixed JSON format

- **Comparable.** Ten protocols written with the same keys can be counted by a program ("recurrence
  used in 8 of 10 protocols"). Free text, or keys that change from protocol to protocol, can't be.
- **Checkable.** Every rule has `evidence.page` and `evidence.quote`, so a program can look the
  quote up in the PDF and flag invented ones. A schema validator rejects a broken reply before it is
  saved.
- **Explicit gaps.** A key is never left out. `null` means "doesn't apply", while `"not_stated"`
  means "the protocol doesn't say". Those are different findings, and an empty or missing key would
  hide the difference.
- **Stable vocabulary.** Element names come from a fixed list. A rule that needs something new goes
  in `new_elements`, so new building blocks are visible and countable instead of hidden under
  different names.
- **The AI interprets, the program counts.** Hashes, page counts, quote checks and all totals are
  filled in by code, because code is exact and the AI isn't reliable at counting.
- **Diffable.** Two runs, or two AI models, on the same protocol can be compared key by key.

## Prompt

````text
I need help designing a solution. Please DISCUSS THE APPROACH FIRST. Do not write code until I say "code".

THE PROBLEM
I have clinical trial protocol PDFs. I want a program where I upload a batch of protocols (for example 10)
and, for EACH protocol, it captures the basic elements that are used to represent that protocol: its design
and, above all, its conditions — what decides when, whether and for whom something happens.

Examples of the kind of content I mean (only examples, not a list to copy):
- "Screening within 28 days before Day 1"
- "Treatment every 21 days for up to 6 cycles, or until disease progression"
- "If ANC < 1.0 × 10^9/L on Day 1, delay the cycle by up to 2 weeks; if not recovered, stop treatment"
- "Participants in Arm B cross over to the active drug after progression"
- "ECG at Cycles 1 and 3 only"
- "Survival follow-up by phone every 12 weeks after the last dose"

The real goal behind this: I want to learn the SMALLEST GENERAL SET of building blocks (a vocabulary)
that can represent ANY protocol — time points, timing patterns, patient facts, conditions, events, actions
and so on. So for each protocol I want to see:
1. which basic elements it uses, and how each rule or condition in it is built from those elements;
2. anything that cannot be represented with the elements found so far (a new element);
3. across all uploaded protocols: which elements repeat, how often, and whether new protocols still add
   new elements or the list has stopped growing.

WHAT THE INPUT LOOKS LIKE
- Protocols are long (often 80–300 pages) and come from any therapy area and design: oncology cycles,
  vaccines, crossover, bioequivalence, devices, paediatric, observational, rare disease.
- Most of the important content sits in schedule-of-activities tables (often spread over several pages,
  with merged cells and tick marks) and in their FOOTNOTES, plus the text sections on dose modification,
  discontinuation and follow-up.
- Some PDFs are scanned images; some have pages cropped or rotated; formats differ between sponsors.

REQUIREMENTS
- Works for any protocol. No protocol-specific code or keyword lists.
- Never guess. If a protocol doesn't state something, the value is "not_stated", never filled in.
- Every rule points to its evidence: a word-for-word quote and a page number, and the program checks that
  the quote really appears in the PDF.
- Results from different protocols must be comparable: every protocol uses the SAME JSON FORMAT below.
- I must be able to review the output easily (the JSON, plus a readable report and a CSV made from it).
- It should cope with re-runs and failures (don't redo or re-pay for protocols already finished).

OUTPUT FORMAT (fixed — the same keys for every protocol)

Why it is fixed: so results can be counted and compared by code across protocols, quotes can be checked
automatically, gaps are explicit, and two runs or two models can be compared key by key.

General rules:
- Never leave a key out. null = does not apply. "not_stated" = the protocol does not say.
- Numbers and units exactly as the protocol writes them. Never convert (6 weeks stays 6 weeks, a month
  is never turned into 30 days).
- Fields marked (program) are filled by code, never by the AI: file_sha256, page_count, checks.*, run.*,
  and every count or total.
- Element names come only from the VOCABULARY below. Anything else goes in new_elements.

One file per protocol: <file name>.json

{
  "schema_version": "1.0",
  "protocol": {
    "file_name": string,
    "file_sha256": string (program),
    "title": string, "protocol_number": string, "version_date": string,
    "phase": string, "therapy_area": string, "design": string (one line),
    "page_count": number (program),
    "pages_not_read": [ { "page": number, "reason": string } ]
  },
  "groups": [ {                                  // arms, cohorts, parts, sequences
    "id": "G1", "name": string,
    "how_assigned": "randomised" | "by_fact" | "chosen" | "single_group" | "not_stated",
    "evidence": EVIDENCE
  } ],
  "time_points": [ {                             // every date the schedule counts from
    "id": "TP1", "name": string,
    "how_dated": "entered" | "offset" | "set_by_activity" | "set_by_event" | "earliest_of" | "latest_of" | "not_stated",
    "from": "TP id" | null,
    "offset": OFFSET | null,
    "window": { "earliest": OFFSET, "latest": OFFSET } | null,
    "evidence": EVIDENCE
  } ],
  "rules": [ {                                   // every rule about WHEN, WHETHER or FOR WHOM
    "id": "R1",
    "evidence": EVIDENCE,
    "plain": string (one sentence in plain words),
    "applies_to": { "groups": ["G id"] | "all", "activities": [string] },
    "when": { "type": "time_point" | "event" | "result_recorded" | "activity_due" | "activity_done",
              "ref": string },
    "if_logic": "all" | "any" | null,
    "if": [ CONDITION ],
    "then": [ ACTION ],
    "timeout": [ ACTION ],                       // what happens if a wait passes its deadline; [] if none
    "elements": [ vocabulary names ],
    "new_elements": [ { "name": string, "definition": string, "why_needed": string } ],
    "not_stated": [ string ],                    // gaps that change dates
    "confidence": "high" | "medium" | "low",
    "checks": { "quote_found": true | false (program) }
  } ],
  "unclear": [ { "page": number, "issue": string } ],
  "run": { "model": string, "prompt_version": string, "run_at": string,
           "quotes_checked": number, "quotes_not_found": ["R id"] }   (program)
}

EVIDENCE  = { "page": number, "location": string (section / table / footnote), "quote": string (≤ 40 words, word for word) }
OFFSET    = { "amount": number | "not_stated", "unit": UNIT, "direction": "before" | "after" }
CONDITION = { "fact": string, "measured": string (when it is known), "aggregation": AGGREGATION,
              "op": OP, "value": number | string | [low, high] | "not_stated", "unit": string | null }
ACTION    = { "action": ACTION_NAME, "target": string, ...only the keys that action needs:
              "recurrence": { "every": OFFSET-without-direction, "count_max": number | "not_stated" | null,
                              "until": [string], "counted_from": "previous_actual" | "fixed_grid" | "not_stated" },
              "occurrences": [number], "amount": OFFSET, "until": CONDITION, "deadline": OFFSET + "from",
              "value": string }

VOCABULARY (starting list; names are frozen once coding starts, only new_elements adds names)
- Things:  activity, group, measure, event
- Time:    time_point, offset, window, duration, recurrence, earliest_of, latest_of
- Logic:   comparison, aggregation, and, or, not, counter
- Actions: schedule, stop, delay, shift, pause_until, skip, repeat, set, change, switch_group, warn, ask
UNIT        = minutes | hours | days | weeks | months | years | business_days | cycles
OP          = "=" | "!=" | "<" | "<=" | ">" | ">=" | "between" | "in"
AGGREGATION = latest | worst | best | count | change_from_baseline | consecutive | persists_for | any_within | all_within
ACTION_NAME = the Actions above

Combined summary over all protocols: summary.json (all program-computed from the per-protocol files)
{
  "schema_version": "1.0",
  "protocols": [ { "file_name", "rules", "rules_with_new_elements", "quotes_not_found" } ],
  "elements": [ { "element", "protocols", "rules", "example": { "file_name", "rule_id", "page", "quote" } } ],
  "new_elements": [ { "name", "definition", "protocols", "merged_from": [names], "rule_refs": [ { "file_name", "rule_id" } ] } ],
  "saturation": [ { "order", "file_name", "new_elements_added", "vocabulary_size" } ]
}
(Merging new elements with the same meaning but different names is the one step that may need an AI call;
 everything else in the summary is plain counting.)

WORKED EXAMPLE (made-up protocol, quotes invented for illustration)
{
  "schema_version": "1.0",
  "protocol": {
    "file_name": "EXAMPLE-ONC-01.pdf", "file_sha256": "(program)",
    "title": "A Phase 2 Study of Drug X in Advanced Solid Tumours", "protocol_number": "EX-ONC-01",
    "version_date": "2025-03-14", "phase": "2", "therapy_area": "oncology",
    "design": "single arm, 21-day cycles for up to 6 cycles, then survival follow-up",
    "page_count": 112, "pages_not_read": [ { "page": 97, "reason": "scanned image, no text" } ]
  },
  "groups": [ { "id": "G1", "name": "All participants", "how_assigned": "single_group",
    "evidence": { "page": 9, "location": "Synopsis", "quote": "This is a single-arm, open-label study." } } ],
  "time_points": [
    { "id": "TP1", "name": "Day 1", "how_dated": "set_by_activity", "from": null, "offset": null, "window": null,
      "evidence": { "page": 18, "location": "Section 6.1", "quote": "Day 1 is the day of the first dose of Drug X." } },
    { "id": "TP2", "name": "Screening", "how_dated": "entered", "from": "TP1", "offset": null,
      "window": { "earliest": { "amount": 28, "unit": "days", "direction": "before" },
                  "latest":   { "amount": 0,  "unit": "days", "direction": "before" } },
      "evidence": { "page": 15, "location": "Table 1", "quote": "Screening assessments must be done within 28 days before Day 1." } }
  ],
  "rules": [
    { "id": "R1",
      "evidence": { "page": 19, "location": "Section 6.2",
        "quote": "Drug X is given on Day 1 of each 21-day cycle for up to 6 cycles or until disease progression, whichever occurs first." },
      "plain": "Dose every 21 days, at most 6 times, stopping early at progression.",
      "applies_to": { "groups": "all", "activities": ["Drug X dose"] },
      "when": { "type": "time_point", "ref": "TP1" },
      "if_logic": null, "if": [],
      "then": [ { "action": "schedule", "target": "Drug X dose",
        "recurrence": { "every": { "amount": 21, "unit": "days" }, "count_max": 6,
                        "until": ["event: disease progression"], "counted_from": "not_stated" } } ],
      "timeout": [],
      "elements": ["activity", "time_point", "recurrence", "event", "earliest_of", "schedule"],
      "new_elements": [],
      "not_stated": ["whether each cycle counts from the previous actual dose or from Day 1"],
      "confidence": "high", "checks": { "quote_found": true } },
    { "id": "R2",
      "evidence": { "page": 24, "location": "Table 5, footnote b",
        "quote": "If ANC is < 1.0 × 10^9/L on Day 1 of a cycle, delay treatment until ANC ≥ 1.0 × 10^9/L. If recovery takes more than 14 days, discontinue Drug X." },
      "plain": "Low ANC on a dosing day holds the dose until it recovers; no recovery in 14 days ends treatment.",
      "applies_to": { "groups": "all", "activities": ["Drug X dose"] },
      "when": { "type": "activity_due", "ref": "Drug X dose, Day 1 of each cycle" },
      "if_logic": "all",
      "if": [ { "fact": "ANC", "measured": "Day 1 of each cycle", "aggregation": "latest",
                "op": "<", "value": 1.0, "unit": "10^9/L" } ],
      "then": [ { "action": "pause_until", "target": "Drug X dose (this cycle)",
        "until": { "fact": "ANC", "measured": "any time", "aggregation": "latest", "op": ">=", "value": 1.0, "unit": "10^9/L" },
        "deadline": { "amount": 14, "unit": "days", "direction": "after", "from": "planned Day 1 of the cycle" } } ],
      "timeout": [ { "action": "stop", "target": "Drug X dosing" } ],
      "elements": ["measure", "comparison", "aggregation", "pause_until", "offset", "stop"],
      "new_elements": [],
      "not_stated": ["whether later cycles move with the delayed cycle"],
      "confidence": "high", "checks": { "quote_found": true } },
    { "id": "R3",
      "evidence": { "page": 15, "location": "Table 1, footnote f", "quote": "ECG pre-dose on Day 1 of Cycles 1 and 3 only." },
      "plain": "ECG only at the first and third cycle.",
      "applies_to": { "groups": "all", "activities": ["ECG"] },
      "when": { "type": "activity_due", "ref": "Drug X dose, Day 1 of each cycle" },
      "if_logic": null, "if": [],
      "then": [ { "action": "schedule", "target": "ECG", "occurrences": [1, 3] } ],
      "timeout": [],
      "elements": ["activity", "recurrence", "schedule"],
      "new_elements": [ { "name": "occurrence_filter",
        "definition": "An activity happens only at listed occurrences of a repeating activity.",
        "why_needed": "recurrence describes the whole series; nothing picks out cycles 1 and 3." } ],
      "not_stated": [], "confidence": "high", "checks": { "quote_found": true } }
  ],
  "unclear": [ { "page": 24, "issue": "Table 5 says 14 days, Section 7.3 says 2 weeks or more; same or different limit?" } ],
  "run": { "model": "(program)", "prompt_version": "1.0", "run_at": "(program)",
           "quotes_checked": 6, "quotes_not_found": [] }
}

WHAT I WANT FROM YOU NOW (the discussion)
1. Ask me the questions you need answered before choosing an approach. Keep them few and specific.
2. Review the OUTPUT FORMAT and VOCABULARY: say what you would add, remove or rename, and why. Keep changes
   small; once we start coding the keys and names are frozen, and growth happens only through new_elements.
   Explain how to stop new_elements growing into duplicates.
3. Propose 2–3 overall approaches (for example: one AI pass per protocol, several focused passes, rule-based
   PDF reading plus AI). For each: how it works step by step, strengths, weaknesses, rough cost and time for
   10 protocols, and how accurate it is likely to be.
4. Recommend one approach and explain why.
5. Explain how we would check that the results are correct (schema validation, quote checks, a manual spot
   check, comparing two runs or two AI models, a small set of protocols we label by hand).
6. List the main risks: long PDFs, tables and footnotes, scanned pages, the AI inventing content, the same
   idea being named differently across protocols.

Keep it plain and practical. When I say "code", build the recommended approach step by step, with tests
(including a JSON schema check of every output file), and run it on 2–3 protocols first so I can check the
output before running all of them.

What I can tell you already (fill in or delete):
[ ] Language / environment I prefer: ...
[ ] AI model or API I can use (and any budget limit): ...
[ ] Where the PDFs are and roughly how many in total (beyond the first 10): ...
````
