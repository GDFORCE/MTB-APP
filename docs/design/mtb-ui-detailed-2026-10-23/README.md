# Detailed screen and option review

Prepared 23 October 2026. **Application code is untouched.**

The latest focused request is the protocol-question flow: [try three one-question-at-a-time designs](../mtb-protocol-questions-2026-10-23/preview.html). That is the current review target. The broader audit below is preserved separately.

## Review artifacts

- [Screen explorer and option register](preview.html): 118 navigable screen states covering 106 existing route files.
- [Route and option placement register](route-and-option-register.md).
- [Keep, move and consolidate decisions](button-decisions.md).
- [Raw source inventory](source-inventory.json).
- [Screen metadata](screen-manifest.json).
- [Browser verification](verification.json).

Open the HTML locally in a browser. It uses bundled fonts and local data scripts, with no backend or application session. App-like interactions are fictional design states; generic action sheets are interaction placeholders, not implemented backend workflows.

## Scope and limits

The source scan covers 223 frontend application/source files. It records 106 route files, 1,757 interactive declaration sites (including 1,253 action-like sites), 477 label/menu configuration entries, 420 input declaration sites, and 1,025 literal option/value arrays. Literal arrays also include non-UI arrays and nested arrays; these counts must not be represented as counts of unique live buttons or unique selectable options.

The 118 screen states include 102 manually configured states using shared layout families and 16 generated shared-pattern/appendix states. “Manually configured” does not mean 102 distinct bespoke layouts or 102 authenticated flows tested end to end. The original-control inspector includes conservative transitive imports: generic helpers and unused named exports can appear. Dynamic labels, server-supplied questions, role checks and option lists remain source expressions rather than invented values.

Each captured declaration remains accessible in the searchable original-source register, including its file, line, event handler, explicit disabled expression, enclosing conditions and source excerpt. The placement panel maps action families to visible sections or secondary menus. Keyword references in that panel are candidate evidence and require checking the original handler; they are not a proof of semantic equivalence.

This is a design and traceability review, not a claim that every captured source declaration has a distinct polished button. Runtime reachability and permissions still need authenticated verification before an eventual implementation. No capability is approved for deletion in this review. Proposed consolidation affects duplicate entry points; conditional/destructive/review actions stay contextual.

## Findings that changed the first concept

- Restore all sponsor quick actions: Add Trial, Add Site, Submit Schedule and Protocol Review.
- Retain all four clickable sponsor totals, including principal investigators, and keep site performance and recent notifications.
- Preserve the PI footer’s Messages destination. The earlier mockup replaced it with Patients.
- Preserve the CRC dashboard’s distinct six-item navigation, including Actions and Messages.
- Keep the current KIT patient enrollment/scheduling flow distinct from canonical and legacy visit/task/comment screens.
- Keep all medication dose outcomes, corrections, schedule and history.
- Keep patient study information, including medication, risks/side effects, emergency contacts and FAQ.
- Keep day/week/month views and calendar preferences. The actual calendar-settings screen changes saved preferences; it does not connect a calendar service.
- Keep sponsor notification categories and the separate shared notification route.
- Preserve the full remote-audit module, organization administration, admin portal, chat attachments/voice/channel tools, account verification, language and support.
- Current `ScheduleVersionsScreen.tsx` contains parity/read-mode product controls, despite older context describing them as absent. Retain their existing restrictions; do not activate them through a visual refresh.

## Keeping functionality while reducing clutter

Frequent actions stay visible. Less frequent actions get a clearly named group. Duplicate shortcuts, such as trial Edit and Download in both a footer and overflow menu, can be consolidated into one accessible entry each. A developer-only or unreferenced route is not promoted into product navigation just because a file exists.

The newest protocol-question concept is deliberately separate from this technical review interface: one question per screen, expandable evidence, Back to change an answer, and one final review/confirmation. The source inspection panels belong to the design-review tool, not to the patient or clinical application.

No implementation prompt, deployment, clinical rule change, data migration, authentication change or read-mode change is included. Existing prompt documents were not modified.
