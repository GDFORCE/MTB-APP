# My Trial Board — independent UI concept

Prepared 23 October 2026. **Design artifacts only. Application code is untouched.**

Open [preview.html](preview.html) in a browser to review the complete design board. It runs locally without a server or network dependencies. Scroll inside each phone to see its remaining content; section links navigate between mockups. Other controls and all data are illustrative.

## Review files

- [Before and after: sponsor dashboard](00-before-and-after.png)
- [Sponsor, clinical and patient workspaces](01-daily-workspaces.png)
- [Trial directory, clinical patient schedule and visit detail](02-study-and-visit-flows.png)
- [Sign-in and patient appointment details](03-access-and-patient-details.png)
- [Desktop sponsor workspace](screens/desktop.png)
- [All nine screen previews as a PDF](mtb-ui-design-concept.pdf)
- [Design rationale and review notes](design-notes.md)

There are eight mobile mockups and one desktop mockup. PNGs are exported at twice the logical resolution. Mobile viewports are 390 × 844; the desktop composition is 1408 × 1020. The PDF contains a preview of each screen, including only the visible viewport for screens with additional scrollable content.

`current-ui/` contains browser captures of the existing welcome, sign-in, sponsor dashboard and trial directory. Sponsor captures use browser-intercepted fictional API responses. They show existing rendering with sample data, not an actual sponsor account. No credentials were used, and the captures did not write to the backend.

All names, trial identifiers, counts, dates, procedures, sites and appointments in the concept are fictional samples. They are not clinical instructions or protocol interpretations.

The desktop navigation, search, keyboard shortcut and recruitment chart are proposed design extensions. This board does not establish that those capabilities already exist. Role-specific views are separate: patient links lead to the patient appointment mockup; clinical task editing appears only in the clinical mockup.

No implementation prompt, deployment, data migration, scheduling-engine change, authentication change or clinical policy change is included or authorized by these artifacts. Historical prompt files and the current implementation prompt were left alone.
