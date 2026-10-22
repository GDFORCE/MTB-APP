# Extracted schedule UI review

Reviewed 21 October 2026. Design recommendations only; no app code or scheduling policy changed. This is a review, not an implementation or continuation prompt, and does not supersede the current implementation instructions.

## Scope and evidence

Reviewed the current working-tree implementations of `ExtractionPreview.tsx`, `SimpleScheduleTable.tsx`, `ProtocolScheduleScreen.tsx`, `VisitScheduleReview.tsx`, `visitSchedule.ts`, `PlaneReviewPanel.tsx`, and the Add Trial integration. Also inspected saved phone screenshots in `docs/audits/ui-review/` (September; historical appearance only) and the synthetic native manual-rule journey screenshot under `docs/evidence/p7/2026-10-19-final/` (October). This was not a fresh live browser walkthrough or a usability study.

Existing strengths worth retaining: expandable visits, linked protocol evidence, explicit unstated values, backend-provided approval gates, shared questions answered once for multiple visits, and separate scheduled/repeating/conditional groups.

## Main recommendation

Make the first view answer four questions: What visit is this? When does it happen? Who does it apply to? Does anything need my attention?

Use three layers:

1. **Schedule overview:** short visit cards on phones, a compact table on larger screens. Keep important conditions, repetition and unresolved timing visible without expansion.
2. **Visit details:** assessments, timing explanation, attendance, restrictions and linked source passages.
3. **Review queue:** problems requiring decisions, grouped by their shared cause, with evidence and affected visits beside the decision.

Simplifying presentation must preserve every rule, restriction, alternative, source reference and unknown value. A generic fallback must keep an unfamiliar rule visible as requiring review.

## Findings in the current UI

| Priority | Observation | Recommended change |
|---|---|---|
| First | `ExtractionPreview.tsx:27` calls all review notes optional, while its issue rendering distinguishes blocking findings. Saving a draft and approving a schedule are different actions. | Show open approval blockers separately from informational notes. Example: "3 items need review before approval. You can save this draft." Do not infer approval eligibility from preview counts. |
| First | `ProtocolScheduleScreen.tsx:243` places source coverage, document issues, footnotes and all shared-question forms before the visit list. | Keep one visible attention banner with issue counts and a review action. Put full forms in the review queue. Keep a visible alert for failed/partial extraction and coverage problems. |
| First | `VisitScheduleReview.tsx` uses four flexible columns and small text even on phones. | Use vertically arranged phone cards, with status beside the title and labeled timing/window lines below. Keep desktop columns. Validate the breakpoint against actual content and larger font settings. |
| First | Pending/Ready describes row review state; unsupported rules are a separate flag. Neither describes a patient's appointment state. | Use plain review labels such as "Needs clarification" and "No open questions". Show automation limitations separately. Keep version-level Draft/In review/Approved status distinct. |
| Next | Expanding a row exposes assessments, anchors, all constraints, statements, evidence and questions together. | Open a short explanation and assessment list first. Provide clearly labeled disclosure sections for all rules and protocol source. Never hide a decision-critical condition only in the source section. |
| Next | `PlaneReviewPanel.tsx:70` renders every row and most details in a long sequence. It also repeats summaries from different structures. | Reuse the same visual visit-card pattern for this surface, through an adapter for its own response contract. Share presentation without changing the underlying storage or approval workflow. |
| Next | `PlaneReviewPanel.tsx:83` labels raw JSON "exact stated rule". | Show the actual protocol quote/page as "Protocol wording" and a readable rule explanation as "How scheduling works". Keep structured JSON in an advanced inspection/export area. |
| Next | Source evidence is principally quoted text; preview evidence is one concatenated modal. | Provide per-rule source links. Where authorized document viewing is supported, open the page and highlight the cited passage. A text quote with a clear page label is the fallback. Distinguish PDF page index from printed page number when both exist. |
| Next | `ProtocolScheduleScreen.tsx:191` opens a row but does not scroll it into view. | Review-queue links should bring the affected visit into view and move focus appropriately. Preserve the return path to the question. |
| Later | Export, add visit and track controls compete for space. | Keep the current task's action prominent. Move exports into a menu; expose advanced controls according to capability. Show a readable explanation whenever approval is unavailable. |

## Proposed first screen

Illustrative content only, not extracted clinical instructions:

```text
Review extracted schedule                         Draft
Protocol ABC · Version 2 · Main study
These are protocol rules. Patient dates are calculated later.

12 visit rules · 3 need clarification
2 shared questions affect those 3 visits           Review items
Source check: incomplete                           View gaps

All visits | Needs clarification | No open questions

Treatment
------------------------------------------------------------
Cycle 1 Day 1                       No open questions
When: On the first-dose date
Allowed window: Same day only
Applies to: All participants
4 assessments · Protocol page 23                   View details
------------------------------------------------------------
Weekly follow-up                   Needs clarification
Starts: 7 days after the last actual dose
Repeats: Every 7 days
Ends: Not stated — needs clarification
Allowed window: Not stated
3 assessments · Protocol page 25                   Review item
------------------------------------------------------------

Continue reviewing
```

Every example label must come from the actual rule. "Same day only" requires an explicitly stated zero window. "Not stated" must never become zero. Do not show hypothetical counts or example dates in the real UI.

Count visit definitions separately from generated occurrences. A repeating rule is one definition; label any expanded list as an occurrence preview and describe its bounds. Shared questions, affected visits and blocking findings overlap and must not be added together into a misleading total.

## Display patterns for the main case families

| Case | Simple display | Detail or review requirement |
|---|---|---|
| Fixed date or one-time relative visit | "On [stated date]" or "7 days after [named event]" | Preserve the original protocol label and day-count convention. |
| Protocol Day 1 | "Day 1 of [named phase/cycle]" | Explain its reference. Never silently treat it as offset zero or baseline. |
| Actual-date dependency | "7 days after the actual treatment date" | In protocol review, explain the dependency. In patient scheduling, show "Waiting for actual treatment date" only when that patient's input is missing. |
| Planned or actual reference | "Counted from a planned or actual [event] date" | Show exactly which basis the rule permits. Patient provisional dates must be labeled provisional. |
| Symmetric or asymmetric window | "Up to 3 days before or 7 days after" | Preserve units, endpoints and whether this is a visit or assessment window. |
| Window missing, unclear, or irrelevant | "Not stated", "Needs clarification", or "Not applicable" | Keep these three meanings distinct. |
| Fixed repeating series | "Every 4 weeks from [reference]" | Show first occurrence, counting basis and stated stopping rule separately. |
| Repeat from previous actual visit | "Every 4 weeks after the previous actual visit" | Make the dependency explicit; do not describe this as a fixed calendar series. |
| Treatment cycles | "Each cycle: Day 1 and Day 8" | Explain cycle length and whether the next start follows nominal timing, a previous actual start or a manual decision. Unknown basis remains unresolved. |
| Changing follow-up frequency | "Every 3 months, then every 6 months" | Show the exact stated transition and end condition. Missing transitions require review. |
| Conditional or optional visit | "Only if [condition]" or "Optional: [visit]" | Explain the trigger and who it applies to. Conditional does not mean unresolved or canceled. A repeating conditional visit carries both labels. |
| Unscheduled visit | "When clinically indicated" only if stated | Keep it outside a dated timeline. Do not manufacture a date or cadence. |
| Arm/cohort/substudy restriction | "Arm B only" | Keep population visible in the overview. Offer an All groups view and an explicit group selector; never silently exclude rules. |
| Parallel treatment/lab/imaging schedules | Named sections or explicit selectors | Keep cross-schedule dependencies visible. Do not merge separate definitions just because visit labels match. |
| Activity timing and sequence | "Blood sample: 30 minutes before infusion" | Preserve activity reference, tolerance, requirement and conditions. A visit window does not replace an activity window. |
| Multiple timing constraints | "7 days after treatment; also before the next dose" | Show both conditions, or an explicit "+1 timing restriction" marker beside the summary. Distinguish AND from OR. |
| Footnote modifies a rule | Short footnote effect badge on affected visit/activity | Show full footnote, page, affected items and resolution action together. Unknown scope must remain unresolved. |
| Conflicting passages | "Two protocol instructions disagree" | Compare both quotes side by side. Require a recorded authorized decision; never choose one silently. |
| Unclear/unsupported rule | "Timing needs clarification" or "Cannot calculate automatically" | Show what is missing, affected visits and permitted next actions. Approved manual handling remains clearly manual and produces no invented automatic target. |
| Hold/resume/stop behavior | "After a pause: [stated behavior]" | Explain which later visits move and which remain fixed. Unknown resume policy stays unresolved. |
| Partial, failed or empty extraction | "Some pages could not be processed", "Extraction failed", or "No visits found" | Preserve usable draft content; show missing coverage and the appropriate retry/re-upload action. Do not describe a provider failure as a bad PDF. |
| Amendment or multiple versions | Explicit protocol/version header | Compare changes in a dedicated view. Approval must not imply existing patients have adopted the version. |
| Unknown new case | "Rule needs specialist review" with original wording | Preserve the rule and source, keep the applicable gate, and avoid squeezing it into an inaccurate familiar pattern. |

Do not use a calendar as the default extraction view: many valid rules do not yet have patient dates. An optional rule timeline can help, provided it keeps different reference events, unresolved rules and conditional rules explicit.

## Review queue

Each item should contain: a plain question, why it matters, affected visits/assessments, protocol quotes/pages, permitted choices, a recorded reason where required, and one clear action. Show the impact of a shared answer before saving it. Group duplicate symptoms under their shared cause without hiding independent blocking findings.

Keep informational notes separate from decisions. Acknowledging a note must not be presented as resolving an unclear clinical rule. Preserve independent reviewer requirements, access checks, revision protection and backend approval gates.

For review status, prefer "No open questions" to "Verified" or "Confirmed" unless a human review was actually recorded. Use "Approved" only for the whole approved version. A row can have no open questions while still using an approved manual workflow.

## Suggested order

Start with truthful preview messaging, phone cards, a single attention summary and a focused review queue. Then improve visit detail sections, per-rule evidence navigation and consistent presentation across review surfaces. Add optional timelines or scenario previews only after the core states are understandable.

Validate with representative fixtures from each case family and with sponsor/coordinator/reviewer users. Ask them to identify the reference event, allowed window, population, repetition basis, unresolved decision and next action. Include small phones, large text, keyboard/screen-reader use, partial extraction, permission denial and concurrent edits. Review notes alone cannot establish that every user or every protocol is covered.

## Supporting accessibility guidance

The recommendations to use plain instructions and make the current step and next action clear align with [W3C writing guidance](https://www.w3.org/WAI/tips/writing/) and [W3C's Make Each Step Clear pattern](https://www.w3.org/WAI/WCAG2/supplemental/patterns/o1p04-clear-steps/). These sources support the general design principles; the app-specific findings above come from this repository.
