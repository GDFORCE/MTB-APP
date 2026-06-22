# My Trial Board — PRD v3 (PI + CRC + Sponsor flows complete)

## Status

### v1 — Foundation ✅
- Dawn Rounds design tokens, JWT auth, WebSocket chat, role-aware dashboards.

### v2 — Patient role end-to-end ✅
- My Trial, My Visits, Visit Detail, Medication Reminder, About Trial, Profile, Notifications, Calendar (placeholder).

### v3 — Clinical (PI + CRC) + Sponsor end-to-end ✅
**Shared clinical screens** (used by both PI & CRC):
- `/clinical/my-trials` — list with dawn-gradient cards + per-trial enrolled count
- `/clinical/patients` — search + status filter chips + (+) Add Patient FAB
- `/clinical/add-patient` — full form with trial selector
- `/clinical/visit-detail` — clinical view: patient header, contact info, visit timeline with Mark complete / Reschedule actions
- `/clinical/team` — team directory with online status, invite member CTA
- `/clinical/invite-patient` — send invite via email/phone, success state
- `/clinical/schedule-review` — CRC's daily queue with Approve / Flag / Open actions
- `/clinical/trial-summary` — dawn hero + stats + overview + visit schedule template + Edit/Share buttons

**Sponsor screens**:
- `/sponsor/add-trial` — title, protocol ID, phase, condition, description → flows into visit-schedule builder
- `/sponsor/visit-schedule` — editor for visit rows (name, day offset, ±window, activities) with add/remove
- `/sponsor/share-schedule` — share via email / secure link / PDF with selection cards

**Dashboard wiring**: bottom-tab now role-aware:
- Patient: Home / My Trial / Calendar / Chat / Alerts / Me
- PI/CRC: Home / Trials / Patients / Calendar / Chat / Team / Alerts
- Sponsor/CRO: Home / Trials / Patients / Calendar / Chat / Team / Alerts

Trial cards on dashboard → trial-summary. Patient cards → clinical visit-detail. "Your trials" / "Patients" "See all" actions wired.

## Deferred (iteration 4+)
- **Admin portal** (16 screens: admin-dashboard, action-center, audit-log, delegation, emergency-access, invitation-mgmt, master-data, messages, my-profile, notification-monitoring, org-mgmt, reports, support-ticket, system-alerts, terms-mgmt, trial-monitoring, user-mgmt, user-org-mgmt)
- i18n (multi-language), Emergent push notifications, PDF/CSV export
- Patient Calendar grid (left for user to code)

## Demo seed
`POST /api/seed` creates 4 demo users (Password1!), 1 trial (Protocol-001), 10 visits, 5 patients, sample notifications.
