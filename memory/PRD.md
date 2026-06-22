# My Trial Board — Product Requirements (v2)

## Status (Iteration 2 — Patient role complete)

### v1 — Foundation ✅
- Dawn Rounds design tokens (cream paper, plum ink, raspberry-rose, apricot→rose gradient)
- JWT auth (FastAPI + bcrypt), JWT refresh, forgot-password OTP flow
- Welcome → Entity-type → Register / Sign-in → Forgot-password
- Unified role-aware dashboard for Patient / PI / CRC / Sponsor
- Real-time WebSocket chat (1-to-1 conversations, typing, read receipts, online status)
- Notifications list & mark-as-read
- MongoDB models: users, trials, visits, patients, notifications, conversations, messages
- Idempotent demo seed (POST /api/seed)

### v2 — Patient role end-to-end ✅
- My Trial Hub (`/patient/my-trial`) with 3 inner tabs:
  - **Visits**: timeline with spine, numbered nodes, completed/next/scheduled status, per-visit cards
  - **Medications**: today/schedule/history sub-tabs with Taken/Not taken/Skip actions
  - **Progress**: stats grid + visit completion bar + adherence bar
- My Visits (`/patient/my-visits`): trial picker → trial summary with dawn-gradient hero → all visits list with status rails
- Visit Detail (`/patient/visit-detail/[id]`): dawn-gradient hero + "Before you come in" + "Clinical tasks" + Contact PI
- Medication Reminder (`/patient/medication-reminder`): per-dose toggles + add reminder
- About Trial (`/patient/about-trial`): study title, overview, phase, condition, sponsor, PI, what to expect
- Profile & Settings (`/patient/profile`): avatar, edit profile, change password, notifications, T&C, help, logout
- Notifications (`/notifications`): full list with mark-as-read
- Patient Calendar (`/patient/calendar`): placeholder with Day/Week/Month tabs + buttons (user wires calendar himself)
- Patient bottom-nav fully wired (Home / My Trial / Calendar / Chat / Alerts / Me)

### Deferred (iteration 3+)
- **PI dashboard** flow: My Trials, Patient List, Add Patient, Visit Detail (clinical view), Team, Invite Patient
- **CRC dashboard** flow: Patients, Add Patient, Schedule Review, Team
- **Sponsor dashboard** flow: Trials, Add Trial, Visit Schedule editor, Trial Summary, Share Schedule
- **Admin portal** (16 screens)
- i18n (multi-language), push notifications, PDF export

## Demo seed
`POST /api/seed` creates 4 demo users (Password1!), 1 trial (Protocol-001), 10 visits, 5 patients, sample notifications.
