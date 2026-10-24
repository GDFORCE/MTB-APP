# Route and option placement register

Snapshot: 23 October 2026. 118 screen states across 106 route files. Source control sites are declarations, not unique runtime buttons.

The prototype includes an Original controls panel for every screen. All 1,757 captured declaration sites remain in the searchable source register; this is not a claim that each is a distinct polished button. Auto-generated shared-pattern pages and unresolved dynamic labels are identified explicitly.

## Your trials, in focus. — sponsor-home

- Existing route: `/(app)/sponsor/dashboard`
- Design: Authored layout
- Context: Restores all four clickable totals, all four quick actions, site performance, bell and profile. Submit Schedule still starts by selecting a trial.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Trial | Primary action |
| Add Site | Visible section / toolbar |
| Submit Schedule | Visible section / toolbar |
| Protocol Review | Visible section / toolbar |
| Total Trials | Visible section / toolbar |
| Total Sites | Visible section / toolbar |
| Total Patients | Visible section / toolbar |
| Total PIs | Visible section / toolbar |
| Site performance | Visible section / toolbar |
| Notifications | Visible section / toolbar |
| Refresh dashboard | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/dashboard.tsx`, `frontend/src/features/sponsor/types.ts`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`

## Trials — trials

- Existing route: `/(app)/sponsor/trials`
- Design: Authored layout
- Context: Keep every phase filter and the Terminated state, not just the example chips shown in v1.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Trial | Primary action |
| Search trials | Visible section / toolbar |
| Clear search | Visible section / toolbar |
| Phase filter | Visible section / toolbar |
| All | Visible section / toolbar |
| Active | Visible section / toolbar |
| Completed | Visible section / toolbar |
| Terminated | Visible section / toolbar |
| Open trial | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/trials.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/sponsor/types.ts`

## Create a trial — add-trial

- Existing route: `/(app)/sponsor/add-trial`
- Design: Authored layout
- Context: Creation responsibility acknowledgement, upload, identifier lookup, protocol questions, retry and error recovery are separate retained steps. Locked metadata remains locked.

| Existing option / action family | Proposed placement |
| --- | --- |
| Create Trial | Primary action |
| I Confirm & Create Trial | Visible section / toolbar |
| Choose protocol PDF or text PDF | Visible section / toolbar |
| Look up protocol | Visible section / toolbar |
| Review my answers to the protocol questions | Visible section / toolbar |
| Show trial details | Visible section / toolbar |
| Retry Visit Schedule build | Visible section / toolbar |
| Open Protocol & Schedule Versions | Visible section / toolbar |
| Cancel | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/add-trial.tsx`, `frontend/src/features/uctsm/types.ts`, `frontend/src/features/uctsm/ExtractionPreview.tsx`, `frontend/src/features/uctsm/SimpleScheduleTable.tsx`, `frontend/src/features/uctsm/presentation.ts`, `frontend/src/features/uctsm/ScheduleTable.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/KitLoading.tsx`, `frontend/src/features/kit/KitQuestionsModal.tsx`, `frontend/src/features/kit/KitSchedule.tsx`, `frontend/src/features/uctsm/ProtocolIdentityNotice.tsx`, `frontend/src/features/uctsm/protocolIdentity.ts`

## Protocol questions — protocol-questions

- Existing route: `/(app)/sponsor/add-trial`
- Design: Authored layout
- Context: Only protocol-provided options are offered. Unanswered required site settings block continuation. Recommendations are evidence-supplied, never guessed.

| Existing option / action family | Proposed placement |
| --- | --- |
| Confirm and continue | Primary action |
| Review answers | Visible section / toolbar |
| Site settings | Visible section / toolbar |
| Cancel | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/add-trial.tsx`, `frontend/src/features/kit/KitQuestionsModal.tsx`, `frontend/src/features/uctsm/types.ts`, `frontend/src/features/uctsm/ExtractionPreview.tsx`, `frontend/src/features/uctsm/SimpleScheduleTable.tsx`, `frontend/src/features/uctsm/presentation.ts`, `frontend/src/features/uctsm/ScheduleTable.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/KitLoading.tsx`, `frontend/src/features/kit/KitSchedule.tsx`, `frontend/src/features/uctsm/ProtocolIdentityNotice.tsx`, `frontend/src/features/uctsm/protocolIdentity.ts`

## Cardiovascular outcomes — trial-summary

- Existing route: `/(app)/clinical/trial-summary`
- Design: Authored layout
- Context: Sample section tabs reorganize existing summary sections. Duplicate Edit and Download placements become one clearly named overflow menu; functionality remains.

| Existing option / action family | Proposed placement |
| --- | --- |
| Open schedule | Primary action |
| Add Site | Visible section / toolbar |
| Add Patient | Visible section / toolbar |
| Reviewed protocol schedule | Visible section / toolbar |
| Open protocol and schedule versions | Visible section / toolbar |
| Download document | Visible section / toolbar |
| Upload document | Visible section / toolbar |
| Contact team by email | Visible section / toolbar |
| Contact team by phone | Visible section / toolbar |
| Edit Trial | Named secondary menu |
| Download Protocol | Named secondary menu |

Source files: `frontend/app/(app)/clinical/trial-summary.tsx`, `frontend/src/features/uctsm/types.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`, `frontend/src/features/sponsor/types.ts`

## Study sites — sites

- Existing route: `/(app)/sponsor/sites`
- Design: Authored layout
- Context: Site management remains directly reachable from sponsor home and profile. Both file upload and manual site entry remain.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Site | Primary action |
| Search sites | Visible section / toolbar |
| Clear search | Visible section / toolbar |
| Status filters | Visible section / toolbar |
| Open site | Visible section / toolbar |
| Open linked trial | Visible section / toolbar |
| Call PI or CRC | Visible section / toolbar |
| Email PI or CRC | Visible section / toolbar |
| Refresh sites | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/sites.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/sponsor/types.ts`

## Add a study site — add-site

- Existing route: `/(app)/sponsor/sites`
- Design: Authored layout
- Context: Full, restricted and view-only access choices remain; preserve PI lookup and invitation behavior.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save & Share with PI | Primary action |
| Upload document | Visible section / toolbar |
| Manual entry | Visible section / toolbar |
| Select trial | Visible section / toolbar |
| Choose site document | Visible section / toolbar |
| Upload document | Visible section / toolbar |
| Cancel | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/sites.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/sponsor/types.ts`

## Principal investigators — investigators

- Existing route: `/(app)/sponsor/principal-investigators`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Search investigators | Visible section / toolbar |
| Open investigator | Visible section / toolbar |
| View assigned trials | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/principal-investigators.tsx`, `frontend/src/features/sponsor/types.ts`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`

## Trial participants — sponsor-participants

- Existing route: `/(app)/sponsor/patients`
- Design: Authored layout
- Context: Sponsor records stay de-identified. Recruitment status and enrollment are not substituted for one another.

| Existing option / action family | Proposed placement |
| --- | --- |
| Search | Visible section / toolbar |
| Filter trial | Visible section / toolbar |
| Filter site | Visible section / toolbar |
| Screened | Visible section / toolbar |
| Screen Fail | Visible section / toolbar |
| Randomized | Visible section / toolbar |
| Withdrawn | Visible section / toolbar |
| Dropout | Visible section / toolbar |
| Follow-up | Visible section / toolbar |
| Completed | Visible section / toolbar |
| Open trial | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/patients.tsx`, `frontend/src/features/sponsor/types.ts`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`

## Reviewed protocol schedule — kit-protocol

- Existing route: `/(app)/sponsor/kit-schedule`
- Design: Authored layout
- Context: Current saved KIT model preview. Keep distinct from canonical schedule versions and their review controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| Open trial | Primary action |
| Expand visit assessments | Visible section / toolbar |
| Events that add or stop visits | Visible section / toolbar |
| Notes and cross-check results | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/kit-schedule.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/KitSchedule.tsx`

## Protocol & schedule versions — versions

- Existing route: `/(app)/sponsor/schedule-versions`
- Design: Authored layout
- Context: Current source includes read-mode controls despite older context claiming no product surface. Preserve existing parity and role gates; no read-mode changes are made by this concept.

| Existing option / action family | Proposed placement |
| --- | --- |
| Build / rebuild schedule | Primary action |
| Open version | Visible section / toolbar |
| Accept this difference | Visible section / toolbar |
| Switch reads to the engine | Visible section / toolbar |
| Move reads back to the operational store | Visible section / toolbar |
| Schedule source & parity | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/schedule-versions.tsx`

## Review visit schedule — protocol-schedule

- Existing route: `/(app)/sponsor/protocol-schedule`
- Design: Authored layout
- Context: Approve, submit and reject retain current state/permission gates. Developer workbench remains explicitly developer-only.

| Existing option / action family | Proposed placement |
| --- | --- |
| Approve schedule | Primary action |
| Expand visit | Visible section / toolbar |
| Add Visit | Visible section / toolbar |
| Tracks and rules | Visible section / toolbar |
| Review footnotes and dependency rules | Visible section / toolbar |
| Send for review | Visible section / toolbar |
| Reject schedule | Visible section / toolbar |
| Retry schedule build | Visible section / toolbar |
| Upload the protocol again | Visible section / toolbar |
| Export JSON | Named secondary menu |
| Export CSV | Named secondary menu |
| Open Protocol & Schedule Versions | Named secondary menu |
| Open development workbench | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/protocol-schedule.tsx`

## Protocol identity review — identity

- Existing route: `/(app)/sponsor/protocol-identity-review`
- Design: Authored layout
- Context: Exact available decisions come from the current source configuration; reason and reviewer capabilities remain required.

| Existing option / action family | Proposed placement |
| --- | --- |
| Record decision | Primary action |
| Review upload | Visible section / toolbar |
| Correct the expected identity | Visible section / toolbar |
| Confirm this document’s identity | Visible section / toolbar |
| Reject this document | Visible section / toolbar |
| Keep in quarantine | Visible section / toolbar |
| Retry extraction | Visible section / toolbar |
| Back to list | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/protocol-identity-review.tsx`

## Schedule review inbox — review-inbox

- Existing route: `/(app)/clinical/schedule-review`
- Design: Authored layout
- Context: The route delegates to the canonical inbox component. Shared-component controls are included in the source register.

| Existing option / action family | Proposed placement |
| --- | --- |
| Review trial schedule | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/schedule-review.tsx`

## Footnotes & dependency rules — footnotes

- Existing route: `/(app)/clinical/protocol-review`
- Design: Authored layout
- Context: Retain protocol evidence, unresolved states, scope choices and dependency choices; no default rule is invented.

| Existing option / action family | Proposed placement |
| --- | --- |
| Choose handling per rule | Visible section / toolbar |
| Review timing scope | Visible section / toolbar |
| Review dependency policy | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/protocol-review.tsx`

## Your workday — pi-home

- Existing route: `/(app)/pi/dashboard`
- Design: Authored layout
- Context: PI/SMO/site capabilities and de-identification remain distinct; same shell does not imply the same permissions.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Patient | Primary action |
| New Trial | Visible section / toolbar |
| Needs Attention | Visible section / toolbar |
| My Audits | Visible section / toolbar |
| Total Trials | Visible section / toolbar |
| Sponsors | Visible section / toolbar |
| Patients | Visible section / toolbar |
| Sites | Visible section / toolbar |
| View week | Visible section / toolbar |
| Update visit | Visible section / toolbar |
| Review schedule | Visible section / toolbar |
| See all trials | Visible section / toolbar |

Source files: `frontend/app/(app)/pi/dashboard.tsx`, `frontend/src/features/clinical/dashboard.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Your coordination board — crc-home

- Existing route: `/(app)/crc/dashboard`
- Design: Authored layout
- Context: CRC home currently has six navigation items; all six are retained in this design rather than dropping Actions or Messages.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Patient | Primary action |
| My Trials | Visible section / toolbar |
| Patients | Visible section / toolbar |
| Actions | Visible section / toolbar |
| Messages | Visible section / toolbar |
| Calendar | Visible section / toolbar |
| Me | Visible section / toolbar |
| Update visit outcome | Visible section / toolbar |
| Complete task | Visible section / toolbar |
| Review schedule | Visible section / toolbar |

Source files: `frontend/app/(app)/crc/dashboard.tsx`, `frontend/src/features/clinical/dashboard.tsx`

## Site operations — site-home

- Existing route: `/(app)/site/dashboard`
- Design: Authored layout
- Context: Operational site view uses the PI dashboard component. Organization administration remains a separate destination.

| Existing option / action family | Proposed placement |
| --- | --- |
| Trials | Visible section / toolbar |
| Sponsors | Visible section / toolbar |
| Patients | Visible section / toolbar |
| Sites | Visible section / toolbar |
| Needs Attention | Visible section / toolbar |
| My Audits | Visible section / toolbar |
| View week | Visible section / toolbar |
| Update visit | Visible section / toolbar |

Source files: `frontend/app/(app)/site/dashboard.tsx`

## My trials — clinical-trials

- Existing route: `/(app)/clinical/my-trials`
- Design: Authored layout
- Context: Add Trial is shown only when the existing role/capability permits it.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Trial | Primary action |
| Search trials | Visible section / toolbar |
| Phase filter | Visible section / toolbar |
| Clear filters | Visible section / toolbar |
| Open trial | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/my-trials.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Patients — patients

- Existing route: `/(app)/clinical/patients`
- Design: Authored layout
- Context: Current patient records route KIT records to clinical/kit-patient; keep the existing distinction for older records.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Patient | Primary action |
| Search patients | Visible section / toolbar |
| Clear search | Visible section / toolbar |
| Filter by trial | Visible section / toolbar |
| Open patient schedule | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/patients.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`

## Add a patient — add-patient

- Existing route: `/(app)/clinical/add-patient`
- Design: Authored layout
- Context: A saved reviewed KIT model is required. Ask its participant questions; preserve duplicate-contact validation and submission blocking. No canonical assignments are substituted.

| Existing option / action family | Proposed placement |
| --- | --- |
| Send invitation | Primary action |
| Use suggested initials | Visible section / toolbar |
| Choose trial | Visible section / toolbar |
| Choose PI | Visible section / toolbar |
| Choose language | Visible section / toolbar |
| Retry PI lookup | Visible section / toolbar |
| Open Organization Members | Visible section / toolbar |
| Answer participant questions | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/add-patient.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/KitVisitList.tsx`, `frontend/src/features/kit/participant.ts`

## SUB-018 · Patient schedule — kit-patient

- Existing route: `/(app)/clinical/kit-patient`
- Design: Authored layout
- Context: Current enrollment scheduling path. Only PI/CRC can edit. Visit completion, decisions, anchors and events are separate saved participant facts.

| Existing option / action family | Proposed placement |
| --- | --- |
| Record a visit | Primary action |
| Open visit | Visible section / toolbar |
| Answer a decision | Visible section / toolbar |
| Clear answer | Visible section / toolbar |
| Event | Visible section / toolbar |
| Set anchor date | Visible section / toolbar |
| Save anchor | Visible section / toolbar |
| Clear anchor | Visible section / toolbar |
| Show all visits | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/kit-patient.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`, `frontend/src/features/kit/KitVisitList.tsx`, `frontend/src/features/kit/KitControls.tsx`

## Record visit — kit-visit

- Existing route: `/(app)/clinical/kit-patient`
- Design: Authored layout
- Context: KIT visit completion is a date record, not the clinical/admin checklist from the legacy visit-detail screen. Warn when later visits move with the actual date.

| Existing option / action family | Proposed placement |
| --- | --- |
| Mark done | Primary action |
| Update date | Visible section / toolbar |
| Undo | Visible section / toolbar |
| Show assessments | Visible section / toolbar |
| Hide assessments | Visible section / toolbar |
| Close | Named secondary menu |

Source files: `frontend/app/(app)/clinical/kit-patient.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`, `frontend/src/features/kit/KitVisitList.tsx`, `frontend/src/features/kit/KitControls.tsx`

## Patient events — kit-events

- Existing route: `/(app)/clinical/kit-patient`
- Design: Authored layout
- Context: Only events in this protocol are offered. Preserve repeatability and terminal-event consequences; do not invent event names or answers.

| Existing option / action family | Proposed placement |
| --- | --- |
| Log event | Primary action |
| Choose event | Visible section / toolbar |
| Answer event questions | Visible section / toolbar |
| Remove event | Visible section / toolbar |
| Close | Named secondary menu |

Source files: `frontend/app/(app)/clinical/kit-patient.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`, `frontend/src/features/kit/KitVisitList.tsx`, `frontend/src/features/kit/KitControls.tsx`

## Operational patient schedule — legacy-patient

- Existing route: `/(app)/clinical/patient-schedule`
- Design: Authored layout
- Context: Separate existing canonical/operational surface. Retained as an advanced existing route, not promoted as the new KIT patient flow.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save unscheduled visit | Primary action |
| Choose patient | Visible section / toolbar |
| Add unscheduled visit | Visible section / toolbar |
| Cancel | Visible section / toolbar |
| Preview schedule impact | Visible section / toolbar |
| Timeline | Visible section / toolbar |
| Table | Visible section / toolbar |
| Confirm condition change | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/patient-schedule.tsx`

## Visit details — legacy-visit

- Existing route: `/(app)/clinical/visit-detail`
- Design: Authored layout
- Context: Keep tasks, comments and administrative editing intact on their current route. The KIT visit screen does not acquire these controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save visit update | Primary action |
| Clinical tasks | Visible section / toolbar |
| Administrative tasks | Visible section / toolbar |
| Add task | Visible section / toolbar |
| Delete task | Visible section / toolbar |
| Complete task | Visible section / toolbar |
| Add comment | Visible section / toolbar |
| Delete comment | Visible section / toolbar |
| Call patient | Visible section / toolbar |
| Message patient | Visible section / toolbar |
| Edit scheduled date | Visible section / toolbar |
| Change outcome | Visible section / toolbar |
| Add to calendar | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/visit-detail.tsx`

## Team calendar — calendar

- Existing route: `/(app)/clinical/team-calendar`
- Design: Authored layout
- Context: Keep all existing view modes, navigation and settings entry.

| Existing option / action family | Proposed placement |
| --- | --- |
| Previous period | Visible section / toolbar |
| Next period | Visible section / toolbar |
| Month | Visible section / toolbar |
| Week | Visible section / toolbar |
| Day | Visible section / toolbar |
| Today | Visible section / toolbar |
| Select day | Visible section / toolbar |
| Refresh | Visible section / toolbar |
| Calendar settings | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/team-calendar.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Calendar settings — calendar-settings

- Existing route: `/(app)/clinical/calendar-settings`
- Design: Authored layout
- Context: Current preferences auto-save per choice with rollback on error. No calendar-service connection or new Save button is introduced.

| Existing option / action family | Proposed placement |
| --- | --- |
| Default view: Day / Week / Month | Visible section / toolbar |
| Start week on: Sunday / Monday | Visible section / toolbar |
| Visit reminders | Visible section / toolbar |
| Medication reminders | Visible section / toolbar |
| 1 day before | Visible section / toolbar |
| 2 days before | Visible section / toolbar |
| 3 days before | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/calendar-settings.tsx`

## Organization members — team

- Existing route: `/(app)/clinical/team`
- Design: Authored layout
- Context: Invite stays capability-gated; keep the distinction between trial/team invitations and platform invitations.

| Existing option / action family | Proposed placement |
| --- | --- |
| Invite organization member | Primary action |
| Search members | Visible section / toolbar |
| Filter role | Visible section / toolbar |
| Open member | Visible section / toolbar |
| Message member | Visible section / toolbar |
| Show assigned trials | Visible section / toolbar |
| Hide assigned trials | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/team.tsx`

## Organization member — team-member

- Existing route: `/(app)/clinical/team-member`
- Design: Authored layout
- Context: Removal retains its current authorization and confirmation behavior.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save member details | Primary action |
| Message organization member | Visible section / toolbar |
| Call | Visible section / toolbar |
| Edit member details | Visible section / toolbar |
| Remove from organization | Named secondary menu |

Source files: `frontend/app/(app)/clinical/team-member.tsx`

## Invite organization member — invite-member

- Existing route: `/(app)/clinical/invite-member`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Send invite | Primary action |
| Choose role | Visible section / toolbar |
| Choose site | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/invite-member.tsx`

## Organization workspace — org-sponsor

- Existing route: `/(app)/org-admin/sponsor`
- Design: Authored layout
- Context: Organization membership actions and ownership transfer remain separate from ordinary sponsor trial actions.

| Existing option / action family | Proposed placement |
| --- | --- |
| Invite organization member | Primary action |
| Trials | Visible section / toolbar |
| Organization Members | Visible section / toolbar |
| Audit | Visible section / toolbar |
| Expand recruitment | Visible section / toolbar |
| Open trial | Visible section / toolbar |
| Approve member | Visible section / toolbar |
| Reject member | Visible section / toolbar |
| Edit trial | Visible section / toolbar |
| Transfer ownership | Visible section / toolbar |

Source files: `frontend/app/(app)/org-admin/sponsor.tsx`

## Hospital administration — org-site

- Existing route: `/(app)/org-admin/site`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Add Patient | Primary action |
| Create trial | Visible section / toolbar |
| Request delegation | Visible section / toolbar |
| Trials | Visible section / toolbar |
| Organization Members | Visible section / toolbar |
| Audit | Visible section / toolbar |
| Request trial access | Visible section / toolbar |
| Open permitted trial | Visible section / toolbar |
| Edit trial | Visible section / toolbar |
| Invite member | Visible section / toolbar |
| Transfer ownership | Visible section / toolbar |

Source files: `frontend/app/(app)/org-admin/site.tsx`

## Managed hospitals — org-smo

- Existing route: `/(app)/org-admin/smo`
- Design: Authored layout
- Context: SMO site administration and clinical operations remain separate views.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add hospital | Primary action |
| Create trial | Visible section / toolbar |
| Add Patient | Visible section / toolbar |
| Request delegation | Visible section / toolbar |
| Trials | Visible section / toolbar |
| Sites | Visible section / toolbar |
| Organization Members | Visible section / toolbar |
| Audit | Visible section / toolbar |
| Request trial access | Visible section / toolbar |
| Assign site | Visible section / toolbar |
| Remove site | Visible section / toolbar |
| Invite member | Visible section / toolbar |
| Transfer ownership | Visible section / toolbar |

Source files: `frontend/app/(app)/org-admin/smo.tsx`

## Trial access requests — access-requests

- Existing route: `/(app)/org-admin/trial-access-requests`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Review request | Visible section / toolbar |
| Approve | Visible section / toolbar |
| Reject | Visible section / toolbar |

Source files: `frontend/app/(app)/org-admin/trial-access-requests.tsx`

## Ownership transfer — ownership

- Existing route: `/(app)/ownership-transfer`
- Design: Authored layout
- Context: Keep the current proposal, accept and decline flow; moving the entry to an overflow menu must not bypass review.

| Existing option / action family | Proposed placement |
| --- | --- |
| View proposal | Visible section / toolbar |
| Accept ownership | Visible section / toolbar |
| Decline | Visible section / toolbar |
| Confirm decline | Visible section / toolbar |
| Cancel | Visible section / toolbar |
| Open organization oversight | Visible section / toolbar |
| Done | Visible section / toolbar |
| Retry | Visible section / toolbar |

Source files: `frontend/app/(app)/ownership-transfer.tsx`

## Hello, Priya. — patient-home

- Existing route: `/(app)/patient/dashboard`
- Design: Authored layout
- Context: Keep appointments and medication access. KIT visit destinations follow the current source rather than the previous generic clinical detail link.

| Existing option / action family | Proposed placement |
| --- | --- |
| View next visit | Visible section / toolbar |
| My Trial | Visible section / toolbar |
| My visits | Visible section / toolbar |
| Medication adherence | Visible section / toolbar |
| View notifications | Visible section / toolbar |
| Contact care team | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/dashboard.tsx`, `frontend/src/features/kit/patientVisits.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## My trial — my-trial

- Existing route: `/(app)/patient/my-trial`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Visits | Visible section / toolbar |
| Medications | Visible section / toolbar |
| Progress | Visible section / toolbar |
| About this study | Visible section / toolbar |
| Open visit | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/my-trial.tsx`, `frontend/src/features/kit/patientVisits.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## My visits — my-visits

- Existing route: `/(app)/patient/my-visits`
- Design: Authored layout
- Context: Read-only KIT schedule for the patient; no staff event or anchor editing.

| Existing option / action family | Proposed placement |
| --- | --- |
| Expand visit details | Visible section / toolbar |
| Show assessments | Visible section / toolbar |
| Show all visits | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/my-visits.tsx`, `frontend/src/features/patient/components/PatientBottomNav.tsx`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/kit/participant.ts`, `frontend/src/features/kit/KitVisitList.tsx`

## Medications — medications

- Existing route: `/(app)/patient/my-trial`
- Design: Authored layout
- Context: Preserve all four dose outcomes and correction/history flows. This is prescribed medication and adherence, not a new standalone reminder collection.

| Existing option / action family | Proposed placement |
| --- | --- |
| Taken | Visible section / toolbar |
| Not taken | Visible section / toolbar |
| Skip | Visible section / toolbar |
| Later | Visible section / toolbar |
| Review or correct a dose | Visible section / toolbar |
| Edit dose record | Visible section / toolbar |
| Cancel correction | Visible section / toolbar |
| Retry | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/my-trial.tsx`, `frontend/src/features/kit/patientVisits.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## About your study — about-study

- Existing route: `/(app)/patient/about-trial`
- Design: Authored layout
- Context: Retain emergency contact and expandable FAQs as well as study, site and care-team information. No sponsor/clinical editing controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| Study overview | Visible section / toolbar |
| Study sites | Visible section / toolbar |
| Care team | Visible section / toolbar |
| Call | Visible section / toolbar |
| Email | Visible section / toolbar |
| Call emergency contact | Visible section / toolbar |
| Expand FAQ | Visible section / toolbar |
| Visit schedule | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/about-trial.tsx`, `frontend/src/features/kit/patientVisits.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`

## Your calendar — patient-calendar

- Existing route: `/(app)/patient/calendar`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Previous period | Visible section / toolbar |
| Next period | Visible section / toolbar |
| Day | Visible section / toolbar |
| Week | Visible section / toolbar |
| Month | Visible section / toolbar |
| Jump to today | Visible section / toolbar |
| Choose date | Visible section / toolbar |
| Refresh calendar | Visible section / toolbar |
| Calendar settings | Visible section / toolbar |
| View details | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/calendar.tsx`, `frontend/src/features/kit/patientVisits.ts`, `frontend/src/features/kit/index.ts`, `frontend/src/features/kit/schedule.ts`, `frontend/src/features/kit/utils.ts`, `frontend/src/features/kit/describe.ts`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## Your visit — patient-visit

- Existing route: `/(app)/patient/visit-detail`
- Design: Authored layout
- Context: Preserve the existing legacy patient detail route. Current KIT patients use My visits instead; the concept does not fabricate an appointment time or modality.

| Existing option / action family | Proposed placement |
| --- | --- |
| View appointment | Visible section / toolbar |
| View assessments | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(app)/patient/visit-detail.tsx`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## Messages — chat

- Existing route: `/(app)/chat`
- Design: Authored layout
- Context: Retain group composition, thread menu, archived messages and unread controls. Admin broadcasts remain a separate service.

| Existing option / action family | Proposed placement |
| --- | --- |
| New message | Primary action |
| All | Visible section / toolbar |
| Unread | Visible section / toolbar |
| Groups | Visible section / toolbar |
| Mark all as read | Visible section / toolbar |
| Archived chats | Visible section / toolbar |
| Open conversation | Visible section / toolbar |
| New group | Visible section / toolbar |
| Notification settings | Named secondary menu |
| Pin / Unpin | Named secondary menu |
| Mute / Unmute | Named secondary menu |
| Archive / Unarchive | Named secondary menu |

Source files: `frontend/app/(app)/chat.tsx`, `frontend/src/features/patient/components/PatientBottomNav.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Study team — thread

- Existing route: `/(app)/chat`
- Design: Authored layout
- Context: Attachments, voice messages and failure handling are retained rather than reduced to a text-only mockup.

| Existing option / action family | Proposed placement |
| --- | --- |
| Send | Primary action |
| Emoji | Visible section / toolbar |
| Document | Visible section / toolbar |
| Gallery | Visible section / toolbar |
| Camera | Visible section / toolbar |
| Microphone | Visible section / toolbar |
| Stop recording | Visible section / toolbar |
| Retry failed send | Visible section / toolbar |
| Discard failed send | Visible section / toolbar |
| View channel info | Visible section / toolbar |
| Search in conversation | Visible section / toolbar |

Source files: `frontend/app/(app)/chat.tsx`, `frontend/src/features/patient/components/PatientBottomNav.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Channel information — channel

- Existing route: `/(app)/conversation/[id]`
- Design: Authored layout
- Context: Keep existing retention controls, policy link and permission gates. No new message-retention rule is introduced.

| Existing option / action family | Proposed placement |
| --- | --- |
| Edit channel | Visible section / toolbar |
| Add member | Visible section / toolbar |
| Invite link | Visible section / toolbar |
| Find member | Visible section / toolbar |
| All files | Visible section / toolbar |
| Notifications | Visible section / toolbar |
| Auto-delete timer | Visible section / toolbar |
| View policy | Visible section / toolbar |
| Set up a similar channel | Visible section / toolbar |
| Clear messages | Named secondary menu |
| Leave group | Named secondary menu |
| Report group | Named secondary menu |

Source files: `frontend/app/(app)/conversation/[id].tsx`

## Notifications — notifications

- Existing route: `/(app)/sponsor/notifications`
- Design: Authored layout
- Context: Sponsor notifications retain their current category filters; shared patient/staff notifications remain a separate route.

| Existing option / action family | Proposed placement |
| --- | --- |
| All | Visible section / toolbar |
| Trials | Visible section / toolbar |
| Sites | Visible section / toolbar |
| Recruitment | Visible section / toolbar |
| System | Visible section / toolbar |
| Mark all read | Visible section / toolbar |
| Open notification | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/notifications.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`

## Your profile — sponsor-profile

- Existing route: `/(app)/sponsor/profile`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Edit Profile | Visible section / toolbar |
| Entity Change | Visible section / toolbar |
| Change Password | Visible section / toolbar |
| Notification Preferences | Visible section / toolbar |
| My Trials | Visible section / toolbar |
| My Sites | Visible section / toolbar |
| Organization Members | Visible section / toolbar |
| Reports | Visible section / toolbar |
| Audit Trail | Visible section / toolbar |
| T&C | Visible section / toolbar |
| Help & Support | Visible section / toolbar |
| Change profile photo | Visible section / toolbar |
| Sign Out | Named secondary menu |

Source files: `frontend/app/(app)/sponsor/profile.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`

## Your profile — clinical-profile

- Existing route: `/(app)/clinical/profile`
- Design: Authored layout
- Context: Managed Hospitals and Trial Access Requests retain their existing role gates.

| Existing option / action family | Proposed placement |
| --- | --- |
| Edit Profile | Visible section / toolbar |
| Entity Change | Visible section / toolbar |
| Change Password | Visible section / toolbar |
| Notification Preferences | Visible section / toolbar |
| My Trials | Visible section / toolbar |
| Organization Members | Visible section / toolbar |
| Managed Hospitals | Visible section / toolbar |
| Trial Access Requests | Visible section / toolbar |
| Reports | Visible section / toolbar |
| Audit Trail | Visible section / toolbar |
| T&C | Visible section / toolbar |
| Help & Support | Visible section / toolbar |
| Change profile photo | Visible section / toolbar |
| Sign Out | Named secondary menu |

Source files: `frontend/app/(app)/clinical/profile/index.tsx`, `frontend/src/features/clinical/components/PiBottomNav.tsx`

## Your profile — patient-profile

- Existing route: `/(app)/patient/profile`
- Design: Authored layout
- Context: Keep language selection, contact OTP verification, preferences and support tickets.

| Existing option / action family | Proposed placement |
| --- | --- |
| Edit Profile | Visible section / toolbar |
| Change Password | Visible section / toolbar |
| Notifications | Visible section / toolbar |
| Language | Visible section / toolbar |
| Terms & Conditions | Visible section / toolbar |
| Privacy Policy | Visible section / toolbar |
| Help & Support | Visible section / toolbar |
| Change profile photo | Visible section / toolbar |
| Log out | Named secondary menu |

Source files: `frontend/app/(app)/patient/profile.tsx`, `frontend/src/features/patient/components/PatientBottomNav.tsx`

## Edit profile — profile-edit

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: edit. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save Changes | Primary action |
| Save Changes | Visible section / toolbar |
| Verify | Visible section / toolbar |
| Resend code | Visible section / toolbar |
| Request an entity change | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Request an entity change — profile-entity-change

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: entity-change. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| Submit Request | Primary action |
| Submit Request | Visible section / toolbar |
| Confirm | Visible section / toolbar |
| Cancel | Visible section / toolbar |
| Done | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Change password — profile-change-password

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: change-password. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| Show password | Primary action |
| Show password | Visible section / toolbar |
| Update Password | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Notification preferences — profile-notifications

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: notifications. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| Save Preferences | Primary action |
| Save Preferences | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Reports — profile-reports

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: reports. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| PDF | Primary action |
| PDF | Visible section / toolbar |
| Excel | Visible section / toolbar |
| Select trials | Visible section / toolbar |
| Select all | Visible section / toolbar |
| Clear all | Visible section / toolbar |
| Generate report | Visible section / toolbar |
| Download report | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Terms & conditions — profile-tnc

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: tnc. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Help & support — profile-help

- Existing route: `/(app)/clinical/profile/[slug]`
- Design: Authored layout
- Context: Existing dynamic profile page: help. Retain contact verification and capability checks for this state.

| Existing option / action family | Proposed placement |
| --- | --- |
| FAQ | Primary action |
| FAQ | Visible section / toolbar |
| Contact Support | Visible section / toolbar |
| My Tickets | Visible section / toolbar |
| Submit Ticket | Visible section / toolbar |
| Raise New Ticket | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/profile/[slug].tsx`

## Audit trail — audit-trail

- Existing route: `/(app)/audit-trail`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Filter action | Visible section / toolbar |
| Filter date | Visible section / toolbar |
| Open record | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/audit-trail.tsx`

## Remote audits — audit-list

- Existing route: `/(app)/sponsor/remote-audit`
- Design: Authored layout
- Context: Preserve the existing audit module; it is not the general audit trail or admin audit log.

| Existing option / action family | Proposed placement |
| --- | --- |
| Open audit | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/remote-audit/index.tsx`, `frontend/src/features/sponsor/components/SponsorBottomNav.tsx`, `frontend/src/features/remote-audit/normalize.ts`, `frontend/src/features/remote-audit/types.ts`

## Site audits — site-audits

- Existing route: `/(app)/site/remote-audit`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Open audit | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/site/remote-audit/index.tsx`, `frontend/src/features/remote-audit/normalize.ts`, `frontend/src/features/remote-audit/types.ts`

## Remote audit workspace — audit-workspace

- Existing route: `/(app)/remote-audit/[auditId]`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Take evidence photo | Visible section / toolbar |
| Upload a document | Visible section / toolbar |
| Scoring and decision | Visible section / toolbar |
| Open query | Visible section / toolbar |
| Open CAPA | Visible section / toolbar |
| Open document | Visible section / toolbar |
| Open virtual session | Visible section / toolbar |

Source files: `frontend/app/(app)/remote-audit/[auditId]/index.tsx`, `frontend/src/features/remote-audit/queriesApi.ts`, `frontend/src/features/remote-audit/queries.ts`, `frontend/src/features/remote-audit/documentsApi.ts`, `frontend/src/features/remote-audit/documentNormalize.ts`, `frontend/src/features/remote-audit/types.ts`, `frontend/src/features/remote-audit/scoringApi.ts`, `frontend/src/features/remote-audit/scoring.ts`, `frontend/src/features/remote-audit/virtualVisits.ts`

## Capture evidence — evidence-capture

- Existing route: `/(app)/site/remote-audit/capture`
- Design: Authored layout
- Context: Camera, consent and capture requirements remain as currently implemented.

| Existing option / action family | Proposed placement |
| --- | --- |
| Capture photo | Primary action |
| Choose lens | Visible section / toolbar |
| Retake | Visible section / toolbar |
| Accept photo | Visible section / toolbar |
| Retry | Visible section / toolbar |
| Upload evidence | Visible section / toolbar |

Source files: `frontend/app/(app)/site/remote-audit/capture.tsx`, `frontend/src/features/remote-audit/normalize.ts`, `frontend/src/features/remote-audit/types.ts`, `frontend/src/features/remote-audit/capture.ts`, `frontend/src/features/remote-audit/manifest.ts`

## Upload audit document — document-upload

- Existing route: `/(app)/site/remote-audit/documents/upload`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Upload document | Primary action |
| Choose file | Visible section / toolbar |
| Remove file | Visible section / toolbar |
| Retry | Visible section / toolbar |

Source files: `frontend/app/(app)/site/remote-audit/documents/upload.tsx`, `frontend/src/features/remote-audit/documentsApi.ts`, `frontend/src/features/remote-audit/documentNormalize.ts`, `frontend/src/features/remote-audit/types.ts`

## Document review — audit-document

- Existing route: `/(app)/sponsor/remote-audit/documents/[id]`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Open file | Visible section / toolbar |
| Download | Visible section / toolbar |
| Review classification | Visible section / toolbar |
| Record document decision | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/remote-audit/documents/[id].tsx`, `frontend/src/features/remote-audit/documentNormalize.ts`, `frontend/src/features/remote-audit/types.ts`, `frontend/src/features/remote-audit/documentsApi.ts`

## Scoring & decision — audit-decision

- Existing route: `/(app)/sponsor/remote-audit/[auditId]/decision`
- Design: Authored layout
- Context: Preserve both scoring axes, blockers and existing step-up-gated sign-off.

| Existing option / action family | Proposed placement |
| --- | --- |
| Sign and record | Primary action |
| Approve | Visible section / toolbar |
| Approve with conditions | Visible section / toolbar |
| Do not approve | Visible section / toolbar |
| Review blockers | Visible section / toolbar |
| Review CAPAs | Visible section / toolbar |

Source files: `frontend/app/(app)/sponsor/remote-audit/[auditId]/decision.tsx`, `frontend/src/features/remote-audit/scoring.ts`, `frontend/src/features/remote-audit/scoringApi.ts`, `frontend/src/features/remote-audit/queries.ts`, `frontend/src/features/remote-audit/virtualVisits.ts`

## Audit query — query

- Existing route: `/(app)/remote-audit/queries/[id]`
- Design: Authored layout
- Context: Actions depend on role and query state. Withdraw remains a reviewed action, not an unguarded Delete.

| Existing option / action family | Proposed placement |
| --- | --- |
| Send response | Primary action |
| Close — response satisfactory | Visible section / toolbar |
| Send back to the site | Visible section / toolbar |
| Reopen this query | Visible section / toolbar |
| Withdraw — raised in error | Visible section / toolbar |

Source files: `frontend/app/(app)/remote-audit/queries/[id].tsx`, `frontend/src/features/remote-audit/queries.ts`, `frontend/src/features/remote-audit/queriesApi.ts`

## Corrective action — capa

- Existing route: `/(app)/remote-audit/capas/[id]`
- Design: Authored layout
- Context: 

| Existing option / action family | Proposed placement |
| --- | --- |
| Start work | Primary action |
| Submit as complete | Visible section / toolbar |
| Effective — close this CAPA | Visible section / toolbar |
| Not effective — send it back | Visible section / toolbar |

Source files: `frontend/app/(app)/remote-audit/capas/[id].tsx`, `frontend/src/features/remote-audit/queries.ts`, `frontend/src/features/remote-audit/queriesApi.ts`

## Virtual audit session — virtual-session

- Existing route: `/(app)/remote-audit/virtual-sessions/[id]`
- Design: Authored layout
- Context: Keep both consent decisions and both join modes. No video-session capability is inferred beyond existing controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| I understand and consent | Visible section / toolbar |
| I do not consent | Visible section / toolbar |
| Join with camera | Visible section / toolbar |
| Join to watch only | Visible section / toolbar |

Source files: `frontend/app/(app)/remote-audit/virtual-sessions/[id].tsx`, `frontend/src/features/remote-audit/scoringApi.ts`, `frontend/src/features/remote-audit/queries.ts`, `frontend/src/features/remote-audit/scoring.ts`, `frontend/src/features/remote-audit/virtualVisits.ts`

## Command-plane schedules — plane

- Existing route: `/(app)/clinical/plane-schedule`
- Design: Authored layout
- Context: Existing pilot and synthetic tooling stays in the advanced area; not mixed into ordinary KIT patient scheduling.

| Existing option / action family | Proposed placement |
| --- | --- |
| Choose patient | Visible section / toolbar |
| Compare protocol versions and match visits | Visible section / toolbar |
| Open synthetic pilot | Visible section / toolbar |
| Add synthetic protocol amendment | Visible section / toolbar |
| Load more | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/plane-schedule.tsx`

## Protocol amendments — amendments

- Existing route: `/(app)/clinical/amendment-review`
- Design: Authored layout
- Context: Keep reviewed event mappings, content-hash approval and opt-in adoption. No patient is adopted by default.

| Existing option / action family | Proposed placement |
| --- | --- |
| Compare versions | Visible section / toolbar |
| Compare with amended version | Visible section / toolbar |
| Confirm | Visible section / toolbar |
| Reject | Visible section / toolbar |
| Save rules and confirm | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/amendment-review.tsx`

## Schedule operations — operations

- Existing route: `/(app)/clinical/plane-operations`
- Design: Authored layout
- Context: Keep current authorization, second-person checks and explicit loss acceptance. These are review-only mock controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| Load failed messages | Visible section / toolbar |
| Replay this message | Visible section / toolbar |
| Create shadow cohort | Visible section / toolbar |
| Check readiness | Visible section / toolbar |
| Promote to pilot | Visible section / toolbar |
| Roll back | Visible section / toolbar |
| Save preferences | Visible section / toolbar |
| Load report | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/plane-operations.tsx`

## Action board — action-board

- Existing route: `/(app)/clinical/action-board`
- Design: Authored layout
- Context: Preserve the CRC Actions destination as well as its dashboard completion controls.

| Existing option / action family | Proposed placement |
| --- | --- |
| Open patient action | Visible section / toolbar |
| Command-plane pilot | Visible section / toolbar |

Source files: `frontend/app/(app)/clinical/action-board.tsx`

## Platform overview — admin-index

- Existing route: `/(app)/admin`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Users | Visible section / toolbar |
| Organizations | Visible section / toolbar |
| Trial Management | Visible section / toolbar |
| Master Data | Visible section / toolbar |
| Support Tickets | Visible section / toolbar |
| System Alerts | Visible section / toolbar |
| Notifications | Visible section / toolbar |
| Audit Logs | Visible section / toolbar |
| Terms & Privacy | Visible section / toolbar |
| Delegation | Visible section / toolbar |
| Emergency Access | Visible section / toolbar |
| Messages | Visible section / toolbar |
| Invitations | Visible section / toolbar |
| Reports | Visible section / toolbar |
| My Profile | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/index.tsx`

## Users — admin-users

- Existing route: `/(app)/admin/users`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add user | Primary action |
| Export CSV | Visible section / toolbar |
| Open user | Visible section / toolbar |
| Unlock account | Visible section / toolbar |
| Suspend user | Visible section / toolbar |
| Activate user | Visible section / toolbar |
| Reset password | Visible section / toolbar |
| Force logout | Visible section / toolbar |
| Send onboarding invitation | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/users.tsx`

## Organizations — admin-organizations

- Existing route: `/(app)/admin/organizations`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add organization | Primary action |
| Open organization | Visible section / toolbar |
| Approve name request | Visible section / toolbar |
| Reject name request | Visible section / toolbar |
| Edit info | Visible section / toolbar |
| Merge into another organization | Visible section / toolbar |
| Activate | Visible section / toolbar |
| Suspend | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/organizations.tsx`

## Trial management — admin-trials

- Existing route: `/(app)/admin/trials`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Filter trials | Visible section / toolbar |
| Open trial | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/trials.tsx`

## Master data — admin-master-data

- Existing route: `/(app)/admin/master-data`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Submissions | Visible section / toolbar |
| Global values | Visible section / toolbar |
| Approve | Visible section / toolbar |
| Edit | Visible section / toolbar |
| Reject | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/master-data.tsx`

## Support tickets — admin-tickets

- Existing route: `/(app)/admin/tickets`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Send response | Primary action |
| Open ticket | Visible section / toolbar |
| Change status | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/tickets.tsx`

## System alerts — admin-alerts

- Existing route: `/(app)/admin/alerts`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Retry delivery | Visible section / toolbar |
| Notify user | Visible section / toolbar |
| Escalate | Visible section / toolbar |
| Resolve | Visible section / toolbar |
| Alert history | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/alerts.tsx`

## Notification monitoring — admin-notification-monitoring

- Existing route: `/(app)/admin/notification-monitoring`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Delivery log | Visible section / toolbar |
| Retries | Visible section / toolbar |
| Configuration | Visible section / toolbar |
| Retry delivery | Visible section / toolbar |
| Save quiet hours | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/notification-monitoring.tsx`

## Audit logs — admin-audit-logs

- Existing route: `/(app)/admin/audit-logs`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Export CSV | Primary action |
| Filter action | Visible section / toolbar |
| Filter role | Visible section / toolbar |
| Group by user | Visible section / toolbar |
| Group by event | Visible section / toolbar |
| Open record | Visible section / toolbar |
| Copy audit ID | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/audit-logs.tsx`

## Terms & privacy — admin-terms

- Existing route: `/(app)/admin/terms`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Publish new version | Primary action |
| Versions | Visible section / toolbar |
| Acceptance history | Visible section / toolbar |
| Edit content | Visible section / toolbar |
| Force re-acceptance for all users | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/terms.tsx`

## Delegation — admin-delegation

- Existing route: `/(app)/admin/delegation`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Add delegation | Primary action |
| Admin tasks | Visible section / toolbar |
| Trial creation requests | Visible section / toolbar |
| Edit tasks & reason | Visible section / toolbar |
| Suspend delegation | Visible section / toolbar |
| Revoke delegation | Visible section / toolbar |
| Approve request | Visible section / toolbar |
| Reject request | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/delegation.tsx`

## Emergency access — admin-emergency-access

- Existing route: `/(app)/admin/emergency-access`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Request access | Primary action |
| Approval inbox | Visible section / toolbar |
| Refresh | Visible section / toolbar |
| Start a new request | Visible section / toolbar |
| Revoke | Visible section / toolbar |
| Approve | Visible section / toolbar |
| Deny | Visible section / toolbar |
| Cancel | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/emergency-access.tsx`

## Broadcast messages — admin-messages

- Existing route: `/(app)/admin/messages`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Send broadcast | Primary action |
| Schedule broadcast | Visible section / toolbar |
| Allow replies | Visible section / toolbar |
| Schedule for later | Visible section / toolbar |
| Send reply | Visible section / toolbar |
| Mark resolved | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/messages.tsx`

## Platform invitations — admin-invitations

- Existing route: `/(app)/admin/invitations`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| New invitation | Primary action |
| Resend invitation | Visible section / toolbar |
| Cancel invitation | Visible section / toolbar |
| Keep | Visible section / toolbar |
| Done | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/invitations.tsx`

## Platform reports — admin-reports

- Existing route: `/(app)/admin/reports`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Generate report | Primary action |
| Report type | Visible section / toolbar |
| Select output format | Visible section / toolbar |
| Download report | Visible section / toolbar |
| Refresh | Visible section / toolbar |

Source files: `frontend/app/(app)/admin/reports.tsx`

## Admin profile — admin-profile

- Existing route: `/(app)/admin/profile`
- Design: Authored layout
- Context: Preserve admin role and delegated-task checks. Admin broadcasts, invitations and audit logs are separate modules.

| Existing option / action family | Proposed placement |
| --- | --- |
| Edit | Primary action |
| Change password | Visible section / toolbar |
| Verify contact | Visible section / toolbar |
| Resend code | Visible section / toolbar |
| Log out | Named secondary menu |

Source files: `frontend/app/(app)/admin/profile.tsx`

## Every visit. Connected. — auth-welcome

- Existing route: `/(auth)/welcome`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Register your organization | Primary action |
| Join with an invite | Visible section / toolbar |
| Sign in | Visible section / toolbar |

Source files: `frontend/app/(auth)/welcome.tsx`

## Welcome back. — auth-sign-in

- Existing route: `/(auth)/sign-in`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Sign In | Primary action |
| Show password | Visible section / toolbar |
| Forgot password | Visible section / toolbar |
| Contact Support | Visible section / toolbar |
| Sign Up | Visible section / toolbar |

Source files: `frontend/app/(auth)/sign-in.tsx`

## Choose your entity — auth-entity-type

- Existing route: `/(auth)/entity-type`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |
| Sponsor | Visible section / toolbar |
| CRO | Visible section / toolbar |
| SMO | Visible section / toolbar |
| Site / Hospital | Visible section / toolbar |

Source files: `frontend/app/(auth)/entity-type.tsx`

## Register your organization — auth-register

- Existing route: `/(auth)/register`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |
| Choose entity | Visible section / toolbar |
| Search organization | Visible section / toolbar |
| Add organization | Visible section / toolbar |
| Choose role | Visible section / toolbar |
| Agree to terms | Visible section / toolbar |

Source files: `frontend/app/(auth)/register.tsx`, `frontend/src/features/auth/registration-validation.ts`, `frontend/src/features/auth/use-hospital-places.ts`

## Verify your phone — auth-verify-phone

- Existing route: `/(auth)/verify-phone`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Verify | Primary action |
| Resend code | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/verify-phone.tsx`, `frontend/src/features/auth/otp-verify.tsx`

## Verify your email — auth-verify-email

- Existing route: `/(auth)/verify-email`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Verify | Primary action |
| Resend code | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/verify-email.tsx`, `frontend/src/features/auth/otp-verify.tsx`

## Set your password — auth-set-password

- Existing route: `/(auth)/set-password`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |
| Show password | Visible section / toolbar |

Source files: `frontend/app/(auth)/set-password.tsx`

## Account recovery — auth-security-questions

- Existing route: `/(auth)/security-questions`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Complete registration | Primary action |
| Select question | Visible section / toolbar |
| Show answer | Visible section / toolbar |

Source files: `frontend/app/(auth)/security-questions.tsx`

## Your account is ready — auth-register-success

- Existing route: `/(auth)/register-success`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |

Source files: `frontend/app/(auth)/register-success.tsx`

## Join your team — auth-join-invite

- Existing route: `/(auth)/join-invite`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/join-invite.tsx`

## Recover your account — auth-forgot-password

- Existing route: `/(auth)/forgot-password`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Continue | Primary action |
| Email recovery | Visible section / toolbar |
| Phone recovery | Visible section / toolbar |
| Security questions | Visible section / toolbar |
| Verify | Visible section / toolbar |
| Resend code | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/forgot-password.tsx`, `frontend/src/features/auth/registration-validation.ts`

## Reset your password — auth-reset-password

- Existing route: `/(auth)/reset-password`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Reset Password | Primary action |
| Show password | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/reset-password.tsx`

## Sign-in support — auth-login-support

- Existing route: `/(auth)/login-support`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| Submit Ticket | Primary action |
| Choose category | Visible section / toolbar |
| Contact support | Visible section / toolbar |

Source files: `frontend/app/(auth)/login-support.tsx`

## Help & support — auth-help-support

- Existing route: `/(auth)/help-support`
- Design: Authored layout
- Context: Existing onboarding route and recovery controls; invitation token schemes remain separate.

| Existing option / action family | Proposed placement |
| --- | --- |
| FAQ | Visible section / toolbar |
| Contact Support | Visible section / toolbar |
| Back | Visible section / toolbar |

Source files: `frontend/app/(auth)/help-support.tsx`

## Invite Patient — route-102

- Existing route: `/(app)/clinical/invite-patient`
- Design: Shared pattern / source review required
- Context: Unreferenced legacy route per project context; preserve in source audit, use current Add Patient flow in main navigation.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/clinical/invite-patient.tsx`

## Sites — route-103

- Existing route: `/(app)/clinical/sites`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/clinical/sites.tsx`, `frontend/src/features/clinical/components/PortfolioDirectory.tsx`

## Sponsors — route-104

- Existing route: `/(app)/clinical/sponsors`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/clinical/sponsors.tsx`, `frontend/src/features/clinical/components/PortfolioDirectory.tsx`

## Files — route-105

- Existing route: `/(app)/conversation/[id]/files`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/conversation/[id]/files.tsx`

## Token — route-106

- Existing route: `/(app)/conversations/join/[token]`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/conversations/join/[token].tsx`

## Dashboard — route-107

- Existing route: `/(app)/dashboard`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/dashboard.tsx`

## Data Policy — route-108

- Existing route: `/(app)/data-policy`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/data-policy.tsx`

## Uctsm Workbench — route-109

- Existing route: `/(app)/dev/uctsm-workbench`
- Design: Shared pattern / source review required
- Context: Developer workbench; keep in developer-only catalog, exclude from product navigation.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/dev/uctsm-workbench.tsx`

## Notifications — route-110

- Existing route: `/(app)/notifications`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/notifications.tsx`

## Medication Reminder — route-111

- Existing route: `/(app)/patient/medication-reminder`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/patient/medication-reminder.tsx`

## Messages — route-112

- Existing route: `/(app)/patient/messages`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/patient/messages.tsx`

## Id — route-113

- Existing route: `/(app)/sponsor/remote-audit/evidence/[id]`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/sponsor/remote-audit/evidence/[id].tsx`, `frontend/src/features/remote-audit/normalize.ts`, `frontend/src/features/remote-audit/types.ts`

## Trial Detail — route-114

- Existing route: `/(app)/sponsor/trial-detail`
- Design: Shared pattern / source review required
- Context: Legacy redirect; no new standalone design. Keep redirect behavior.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/(app)/sponsor/trial-detail.tsx`

## Index — route-115

- Existing route: `/index`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/index.tsx`

## No Internet — route-116

- Existing route: `/no-internet`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/no-internet.tsx`

## Session Timeout — route-117

- Existing route: `/session-timeout`
- Design: Shared pattern / source review required
- Context: Retain destination; source presence alone does not prove runtime reachability.

| Existing option / action family | Proposed placement |
| --- | --- |

Source files: `frontend/app/session-timeout.tsx`
