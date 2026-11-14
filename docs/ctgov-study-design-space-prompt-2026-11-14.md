# Prompt: map every study design and protocol structure on ClinicalTrials.gov

Saved 14 November 2026; revised the same day at the owner's request for full coverage (long run, resumable, ends in a ranked list of what the scheduler is missing). Paste the block below into a fresh Claude session. It needs no earlier conversation. It works in Claude Code (this repository) and on claude.ai with a ClinicalTrials.gov connector; step 5 only applies inside this repository.

This is a research prompt. It changes no app code and does not replace the current implementation prompt, [kit-schedule-fidelity-prompt-2026-11-10.md](kit-schedule-fidelity-prompt-2026-11-10.md). Its results are meant to feed that work.

The registry numbers and addresses in the prompt were checked against the live ClinicalTrials.gov API on 14 November 2026.

```text
GOAL
I build software that turns a clinical-trial protocol into a visit schedule for each patient. For a
month it has given wrong schedules on real protocols, and fixing one protocol at a time has not
converged. I want to stop guessing. Use ClinicalTrials.gov to find everything a trial schedule can be
built from: every kind of study design, every way a protocol lays out its schedule, and every
combination of these. Then tell me exactly which of them my software cannot handle, in order of how
many trials each one affects.

Time is not the limit. This may take many hours and several sessions. Coverage is what matters: I
would rather wait than get a short study that misses things. Do not shorten the work to finish sooner.

WHAT "EVERYTHING" CAN MEAN HERE
The registry holds about 611,000 studies (about 467,000 interventional). There are two kinds of
information, and they need different methods:
- Every study has structured design fields (allocation, intervention model, masking, purpose, phase,
  arms, intervention types). Count these across the whole registry, exactly. No sampling.
- The schedule itself (visits, timing, conditions) is only in the protocol document. About 53,000
  studies have one uploaded. Nobody can read 53,000 protocols, so "everything" here means: every kind
  of design is read several times, and reading goes on until new protocols stop showing anything new.
  The coverage rules in Step 3 define this. Follow them fully.
Keep the two apart in everything you report: exact registry counts are one thing, findings from the
protocols you read are another.

WORKING OVER MANY HOURS
- Keep a progress file from the start and update it after every protocol: the reading plan, each
  protocol's status (planned, read, could not read), and the current building-block list. Save every
  result to a file as you go. Nothing should exist only in the conversation.
- If you are close to running out of room, stop cleanly, save, and say so. When a new session is given
  this same prompt, it must find the progress file, continue from it, and repeat nothing.
- If you can run helpers in parallel, use them for the protocol reading. Give each one the same
  instructions and the current building-block list, and merge their results yourself.
- Do not ask me questions along the way. Where a choice is yours, make it, write it down, and go on.

ACCESS
If you have a ClinicalTrials.gov connector, use it. Otherwise use the public API (no key needed):
- Values and counts of a field across all studies:
  https://clinicaltrials.gov/api/v2/stats/field/values?fields=DesignAllocation,DesignInterventionModel
- How many items a list field holds (for example arms per study):
  https://clinicaltrials.gov/api/v2/stats/field/sizes?fields=ArmGroup
- Count or list the studies matching a combination:
  https://clinicaltrials.gov/api/v2/studies?filter.advanced=AREA[DesignInterventionModel]CROSSOVER AND
  AREA[DesignAllocation]RANDOMIZED&countTotal=true&pageSize=1&fields=NCTId
  (pageSize goes up to 1000; follow nextPageToken for more)
- Studies that have a protocol document: filter.advanced=AREA[LargeDocHasProtocol]true, and ask for
  the field LargeDocumentModule to get each file name.
- The document itself: https://cdn.clinicaltrials.gov/large-docs/<last two digits of the NCT
  number>/<NCT number>/<file name>, for example
  https://cdn.clinicaltrials.gov/large-docs/71/NCT06552871/Prot_SAP_000.pdf
- The full field list is at https://clinicaltrials.gov/data-api/about-api/study-data-structure
Pace your requests and slow down if the API refuses one. Send nothing about me or my project in any
request.

STEP 1: COUNT THE DESIGNS ACROSS THE WHOLE REGISTRY (exact)
For interventional studies, get every value of: allocation, intervention model, masking and who is
masked, primary purpose, phase (a study can list two), number of arms, arm types, intervention types.
Then count the combinations of these, not just each field alone. One way is to page through all
studies asking only for these fields and tally them yourself, which gives any combination from one
pass. Report:
- every combination that occurs, with its count, how many of those studies have a protocol document,
  and three example NCT numbers;
- combinations the registry allows but no study uses;
- combinations that occur but contradict themselves (for example randomized with one group), as a
  note on registry data quality;
- how many studies leave each field empty. An empty field is "not stated". Do not fill it in.
Do the same more briefly for observational studies (model, time perspective) and expanded access.

STEP 2: FIND THE DESIGN FEATURES THE FIELDS DO NOT RECORD
Many design features have no field of their own and appear only in free text: dose escalation and
expansion cohorts, adaptive designs, basket, umbrella and platform trials, run-in and washout periods,
randomized withdrawal, re-randomization, open-label extension, cluster and stepped-wedge
randomization, N-of-1, treatment cycles, and others you find. Start from what you see in the registry
text, not from this list. For each feature, estimate how many studies have it and which Step 1
combinations it goes with. A text search gives an estimate, not a count: read a sample of the matches
for each feature and report how many were real.

STEP 3: READ PROTOCOLS UNTIL NOTHING NEW APPEARS
Build a reading plan from Steps 1 and 2 before reading, and save it. All of these must hold before you
may stop:
a. Every Step 1 combination that covers at least 0.1% of interventional studies has at least 3
   protocols read (all of them if fewer have a document).
b. Every Step 2 feature has at least 5 protocols read.
c. Every phase, every intervention type and every primary purpose has at least 5 protocols read.
d. Rare combinations below 0.1% are not skipped: read at least one for each that has a document, up
   to 50 such combinations, taking the most unusual first.
e. The plan is spread across disease areas, industry and non-industry sponsors, and years, and
   includes long protocols (oncology, vaccines, devices, paediatrics, first-in-human).
f. At least 150 protocols are read in total.
g. After a to f are met, the last 30 protocols read, taken from different parts of the plan, added no
   new building block. If one did, keep reading.
If a part of the plan cannot be met (no documents, unreadable files), record it as not covered. Do
not swap in an easier protocol and call it covered.

In each protocol read the study design section, the schedule of activities with all its footnotes,
and the sections on dosing, dose changes, stopping, withdrawal and follow-up. Read them fully: most
scheduling rules sit in footnotes and in dose-change text, not in the table. Save one file per
protocol containing:
- the design as the registry states it and as the protocol states it (note any difference);
- every rule that decides when or whether something happens, each in plain words with the page and
  the exact quote. That means: what dates are counted from; the periods and what moves a patient from
  one to the next; each visit's timing and allowed window; how visits repeat and when repeating ends;
  visits or tests done only under a condition; what changes when something happens (an event, a
  missed or late visit, a lab result, a dose hold or change, pregnancy, progression, stopping early,
  withdrawal); how arm, cohort, dose level or crossover order changes the schedule; anything decided
  by the investigator's judgment;
- anything unclear or contradictory in the protocol, recorded as unclear. Do not resolve it by
  guessing.
From these rules keep one growing list of building blocks, in plain words, not forced into any fixed
vocabulary. A building block is a general kind of rule (for example "a visit repeats every N days
until an event"), not a value and not one protocol's wording. Give each a short name, a one-line
meaning, and every protocol that uses it.
If a document is scanned or unreadable, list it as not read. Do not guess its content.

CHECK YOUR OWN READING
Choose 15 of the protocols at random and read each one again from scratch, without looking at the
first result. Compare. Report how many rules the first reading missed or got wrong, and what kinds.
If the first reading missed more than about 1 rule in 10, find out why, fix the way you read, and
re-read the protocols affected.

STEP 4: THE COMBINATIONS
Give me:
a. The list of building blocks: meaning, how many of the protocols read use it, and examples with NCT
   number, page and quote.
b. Which building blocks appear together in one protocol, and which depend on each other (for
   example a window on a visit that itself repeats until an event), with the combinations that are
   common, rare, and never seen.
c. Which combinations are impossible by their meaning, and which are possible but you did not see.
   Keep "not seen in what I read" separate from "cannot exist".
d. Which study designs (Steps 1 and 2) bring which building blocks with them (for example, what a
   crossover design always adds to a schedule), and an estimate of what share of registered trials
   needs each building block.
e. The curve of new building blocks against protocols read, so I can see whether it flattened.

STEP 5: WHAT THIS MEANS FOR MY SOFTWARE (only inside the MTB-APP repository)
Read AGENTS.md, docs/kit-schema-element-inventory.md and docs/kit-schedule-fidelity-prompt-2026-11-10.md,
then the kit model code they point to (backend/app/kit, frontend/src/features/kit). Change no app
code. Then:
a. Mark every building block as handled, partly handled, or not handled by the kit model, naming the
   field and giving the reason. Check against the code, not only the inventory document.
b. Do the same for every rule in every protocol file: could the kit model hold it and would the
   schedule engine act on it? Give the share of rules handled per protocol, and the share of
   protocols where every rule is handled.
c. Rank the gaps by how many protocols read, and what share of registered trials, each one affects.
d. Answer this directly, with the evidence: do the gaps reduce to a small set of general building
   blocks that would cover nearly everything once added, or does the list keep growing with every
   protocol? If it keeps growing, say what that means for the fixed-vocabulary approach and what
   kind of approach would not have this problem.
e. Propose a test set: the smallest group of the protocols read (about 20) that together use every
   building block. Their rule files become draft expected answers. Mark them as AI-written drafts
   that a person must check before they are used to score the software.

RULES
- Every statement carries its source: an NCT number for registry facts; NCT number, page and quote for
  protocol facts.
- Never present a finding from the protocols read as a registry-wide fact, and never present a
  text-search estimate as a count.
- Where the registry or a protocol does not state something, write "not stated".
- Put downloaded protocol files in a folder outside the repository. Do not commit them. Keep the NCT
  numbers and addresses so anyone can fetch them again.
- Write for a reader who is not a statistician: plain words, short sentences, a term explained the
  first time it is used.

OUTPUT
1. One report, with a one-page summary first: the designs that cover most trials, the building blocks
   every schedule tool must handle, the rare ones, and (inside the repository) the ranked gaps and the
   answer to 5d.
2. The count tables as data files (CSV or JSON), so the numbers can be rechecked.
3. One rule file per protocol read, and the building-block list.
4. The reading plan with what was met and what was not, the re-reading check result, and what this
   study could not cover and how much that matters.
In the repository, save the report as docs/ctgov-design-space-report-<date>.md and everything else
under docs/protocol-benchmarks/ctgov-survey/ (progress file: progress.json; protocol files:
protocols/<NCT number>.md).
```

## Notes for the owner

- "Everything" is defined by the coverage rules in Step 3, because only about 53,000 of the 611,000 registered studies have a protocol document and nobody can read them all. The rules make the session read every kind of design several times and keep going until 30 protocols in a row add nothing new.
- Expect at least 150 protocols, each often 100 pages or more. That is many hours and several sessions, and heavy usage on the plan. To continue after a session stops, paste the same prompt again; it resumes from the progress file.
- The numbers in Step 3 (0.1%, 3, 5, 50, 150, 30) and the 15 re-read protocols are starting values chosen for this prompt. Raise them for more coverage, lower them for a shorter run.
- This study finds and ranks what the scheduler is missing. It does not change the scheduler. Step 5d and 5e are what feed the fix: whether the fixed vocabulary can ever be completed, and a test set to measure against.
- The 10 November prompt told the session not to download real protocols. This prompt allows it for public ClinicalTrials.gov documents only, kept outside the repository.
