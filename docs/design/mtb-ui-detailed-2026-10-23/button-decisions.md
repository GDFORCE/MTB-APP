# Button decisions — detailed redesign

No functional capability is approved for deletion. Consolidations below concern duplicate entry points or navigation placement.

## Sponsor dashboard quick actions

**KEEP VISIBLE**

**Current:** Add Trial, Add Site, Submit Schedule, Protocol Review

**Proposed:** One primary Add Trial button plus a three-item Quick actions group.

**Reason:** All four are existing workflow entries; none is redundant.

Evidence: `frontend/app/(app)/sponsor/dashboard.tsx`

## Sponsor clickable totals

**KEEP VISIBLE**

**Current:** Total Trials, Total Sites, Total Patients, Total PIs

**Proposed:** Four compact clickable totals with the same destinations.

**Reason:** The first concept omitted a PI total and made some directories harder to reach.

Evidence: `frontend/app/(app)/sponsor/dashboard.tsx`

## Clinical Messages tab

**RESTORE**

**Current:** PI navigation includes Messages.

**Proposed:** Home / My Trials / Messages / Calendar / Me.

**Reason:** The first concept wrongly replaced Messages with Patients. Patient access stays on home and trial detail.

Evidence: `frontend/src/features/clinical/components/PiBottomNav.tsx`

## CRC navigation

**KEEP ALL SIX**

**Current:** Dashboard / Patients / Actions / Messages / Calendar / Me.

**Proposed:** Six compact, minimum 44px touch targets. No destination removed.

**Reason:** CRC has a distinct Actions entry; do not force its footer into the PI navigation model.

Evidence: `frontend/app/(app)/crc/dashboard.tsx`

## Trial Edit and Download duplicates

**CONSOLIDATE SHORTCUTS**

**Current:** Footer Edit Trial / Download Protocol and overflow Edit / Download.

**Proposed:** One labeled Trial actions menu containing Edit Trial and Download Protocol; documents retain per-file downloads.

**Reason:** Same edit/download handlers are repeated. Consolidate duplicate triggers, not the capabilities.

Evidence: `frontend/app/(app)/clinical/trial-summary.tsx`

## KIT and legacy patient visits

**KEEP SEPARATE**

**Current:** KIT date records, decisions, events and anchors; separate legacy tasks/comments route.

**Proposed:** Current KIT schedule first; existing legacy detail kept as its own pattern.

**Reason:** They operate on different saved facts. Visual unification is not permission to change scheduling behavior.

Evidence: `frontend/app/(app)/clinical/kit-patient.tsx`, `frontend/app/(app)/clinical/visit-detail.tsx`

## Patient medication actions

**KEEP VISIBLE**

**Current:** Taken, Not taken, Skip, Later; corrections, schedule and history.

**Proposed:** Four dose outcomes plus Today / Schedule / History and correction panel.

**Reason:** A generic Reminder button would lose real functionality.

Evidence: `frontend/app/(app)/patient/my-trial.tsx`

## Medication reminder route

**KEEP REDIRECT**

**Current:** medication-reminder redirects to My Trial → medications → today.

**Proposed:** One Medications destination; old route stays a redirect.

**Reason:** Avoid designing a second disconnected reminder system.

Evidence: `frontend/app/(app)/patient/medication-reminder.tsx`

## Exports, retention and account tools

**GROUP, DO NOT DELETE**

**Current:** JSON/CSV export, channel retention, reports, preferences, entity change, language and support.

**Proposed:** Named More / Account / Channel info groups, with destructive actions separated.

**Reason:** Frequency is not evidence that a capability is unimportant.

Evidence: `frontend/src/features/uctsm/ProtocolScheduleScreen.tsx`, `frontend/app/(app)/conversation/[id].tsx`, `frontend/app/(app)/patient/profile.tsx`

## Schedule read-mode controls

**KEEP GATED**

**Current:** Current ScheduleVersionsScreen includes parity acceptance and bidirectional read-mode controls.

**Proposed:** Advanced Schedule source & parity section with existing reason, readiness and role gates.

**Reason:** Current source supersedes the older no-product-surface note. Do not remove or activate them through a visual refresh.

Evidence: `frontend/src/features/uctsm/ScheduleVersionsScreen.tsx`

## Developer workbench and orphaned routes

**APPENDIX ONLY**

**Current:** Developer workbench; unreferenced legacy invite screen; redirect trial-detail.

**Proposed:** Preserve source and existing redirects, exclude from ordinary product navigation.

**Reason:** Do not create new access to a route merely because its file exists.

Evidence: `frontend/app/(app)/dev/uctsm-workbench.tsx`, `frontend/app/(app)/clinical/invite-patient.tsx`, `frontend/app/(app)/sponsor/trial-detail.tsx`

## Clinical outcomes and audit decisions

**KEEP CONTEXTUAL**

**Current:** Visit completion/outcome controls; audit queries, CAPAs, consent, decision sign-off.

**Proposed:** Place at the record where their consequences are visible; retain current permission/confirmation checks.

**Reason:** Do not treat conditional or destructive actions as clutter.

Evidence: `frontend/app/(app)/clinical/visit-detail.tsx`, `frontend/app/(app)/remote-audit/queries/[id].tsx`, `frontend/app/(app)/sponsor/remote-audit/[auditId]/decision.tsx`
