# Trial summary page — requirements and role visibility

As of 2026-10-06 · Screen: `frontend/app/(app)/clinical/trial-summary.tsx`

The Trial summary page shows one trial to the site staff and sponsor users who have a relationship with it. Everyone who can open it sees the same seven sections; what differs by role is the actions. Patients appear only as study ID and initials, for every role.

## Who sees what

| Section | PI | CRC | Sponsor / CRO |
| --- | --- | --- | --- |
| Trial header: protocol ID, status, title, CTRI number, phase, disease, drug, duration, total visits, created by and date | View | View | View |
| Recruitment: number of sites, sample size, funnel from screened to completed | View | View | View |
| Sites: name, address, PI, department, PI email, funnel per site | View | View | View, plus Add Site for organisation admins |
| Trial team: name, role, designation, organisation, email, phone | View | View | View |
| Patients: study ID, initials, site, status, visits completed, current visit | View, plus Add Patient | View, plus Add Patient | View only |
| Visit schedule: each schedule version with status and patient count | View | View | View |
| Documents and schedule version history | View, download, upload | View, download, upload | View, download, upload |
| Edit trial details | Only the PI who created the trial | No | Owning organisation only |

Patients, platform admins and anyone with no relationship to the trial cannot open this page. What SMO and Site organisation accounts see is not decided (Q1).

## Rules

- **Who can open it.** PI: created the trial, belongs to its organisation, is assigned to one of its subjects, or accepted an invitation. CRC: the same, without the organisation route. Sponsor and CRO: the trial is owned by or granted to their organisation.
- **Patient privacy.** No role sees a patient's name, phone, email or date of birth here. The Patients section carries a note saying so.
- **Buttons match permissions.** An action button shows only to a user the server will accept the action from.
- **Editable fields.** Title, phase, condition, drug, duration, target enrolment, CTRI number, recruitment status, description. Title, phase and condition are required.
- **Empty and failed states.** Each section has its own empty text. One section failing to load must not blank the rest of the page.

## Not met today

- **SMO and Site accounts get an error.** Their organisation screens link to this page, but the server refuses the recruitment, subjects, team, documents and versions calls for those roles. Found by reading the code, not reproduced in the running app.
- **Trial team shows the viewer, not the sponsor.** The list puts the person viewing first in the sponsor slot, so a PI or CRC never sees the sponsor's contact. A CRO member is labelled "Sponsor".
- **Edit shows to every sponsor and CRO user with access.** The server accepts the save only from the organisation that owns the trial.
- **Add Site shows to every sponsor and CRO user.** The server marks it as allowed for organisation admins only.
- **One failed call blanks the whole page.** All six calls load together.
- **Wrong empty text.** An empty Visit schedule says "Upload a protocol PDF above", but the page has no such control.

## Open questions

- [ ] Q1. What should SMO and Site organisation accounts see on this page?
- [ ] Q2. Should a PI or CRC see every site's subjects and recruitment, or only their own site? Today the subject list and funnel cover the whole trial for every role.
- [ ] Q3. Should a PI or CRC see patient names for their own patients here? Today every role sees study ID and initials only.
