# Recurrence and assessment-condition fixes — 14 September 2026

This focused pass implements general timing and condition fixes. Per the user's remaining usage constraint, no further live model calls or broad corpus runs were made. The broader arbitrary-protocol accuracy objective remains unproven.

## Implemented

- Added `RELATIVE` recurrence termination: an explicit end anchor, calendar offset and inclusive/exclusive boundary. For example, a two-week cadence can stop through six months after baseline without asking the model to invent a count or approximate calendar months as 30 days. New fields are optional for existing termination types; no database migration is needed for the JSON rule.
- Evaluation preserves phase boundaries, respects the materialization horizon and waits when a required end-anchor value has not been recorded. Undefined start/end anchors block validation. Separate fixed-cadence phases can represent a changing schedule; an ambiguous interval such as every 3–4 months still requires an explicit scheduling policy.
- The review table shows relative recurrence endings and their units. Backend repeat summaries also retain the ending rather than labeling the rule as having no maximum.
- Expanded activity-condition instructions to include patient clinical facts and prior results, alongside occurrence/cycle conditions. Named group membership remains activity-level applicability. A conditional assessment with neither a condition nor applicability now blocks approval; a prose footnote alone is insufficient.
- Recurrence horizon comparisons accept mixed date/datetime values using the existing temporal comparison helper.

## Verification

- **508 backend tests passed**, using the curated gate plus recurrence, source-first, extraction and workflow checks: [log](relative-recurrence-backend.log).
- **91 frontend tests passed**: [log](relative-recurrence-frontend.log).
- Scoped TypeScript check passed: [log](relative-recurrence-typecheck.log).
- New checks cover inclusive/exclusive boundaries, a January 31 calendar-month boundary, two successive cadence phases, horizon limits, missing end anchors, serialization and true/false/unknown patient assessment conditions.

The test subprocess explicitly selected the category dispatcher for its legacy scripted-client cases; source-first tests call that pipeline directly. The app's local extractor setting remains unchanged. A pre-existing confinement fixture was made date-independent after its extended discharge date coincided with the actual date; production confinement behavior was unchanged.

These results verify code behavior and preserve prior captured-extraction replays. They do not measure the accuracy of new LLM outputs after the prompt changes. Fresh protocol uploads after a backend restart are required to exercise the changes; saved approved schedules are not re-extracted automatically.
