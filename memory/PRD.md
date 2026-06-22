# My Trial Board — Product Requirements (v1)

## Vision
A warm, premium clinical-trials companion app — "Dawn Rounds" design system — that unites Sponsors, Principal Investigators, Research Coordinators (CRC) and Patients around the same visit schedule.

## v1 scope (this iteration)
- **Design system**: Dawn Rounds tokens (paper-cream, plum ink, raspberry-rose primary, apricot→rose dawn gradient).
- **Auth (JWT, FastAPI + bcrypt)**: register → role select → sign-in → forgot-password (OTP) → reset; secure-store on native, AsyncStorage on web.
- **4 role dashboards** (unified template, role-aware data) for Patient / PI / CRC / Sponsor with hero panel, KPIs/progress ring, trials, visit schedule, patient list, notifications.
- **Real-time chat** (WebSocket): 1-to-1 conversations between any two users, typing indicator, read receipts, online status, conversation list with unread badge.
- **Backend**: FastAPI + MongoDB (motor). Models: users, trials, visits, patients, notifications, conversations, messages. Idempotent demo seed (`POST /api/seed`).

## Deferred to iteration 2
- Per-role tailored deep screens: My Trial Hub, Patient Calendar, Visit Detail, Medication Reminder, About Trial, Profile & Settings, Patient List filters, Add Trial / Visit Schedule editor, Team & Invite, Calendar Settings, Share Schedule, PDF export.
- Admin portal (16 screens).
- Multi-language (i18n) — currently English only.
- Push notifications (Emergent-managed; requires real device build).
- Auto session-timeout banner & no-internet screen.

## Demo seed
`POST /api/seed` creates 4 demo users, 1 trial (Protocol-001), 10 visits, 5 patients, sample notifications.
