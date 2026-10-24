# MTB visual direction: a clearer path through every trial

## What was reviewed

Live local browser review at a 390 × 844 viewport: welcome, sign-in, sponsor overview and sponsor trial directory. Signed-in sponsor screens were rendered with fictional, browser-intercepted API responses in an isolated browser context. These captures are saved in `current-ui/`.

Source review: shared visual tokens, logo, authentication, navigation, sponsor dashboard and trial cards, PI dashboard, clinical dashboard contracts, patient dashboard and navigation, clinical patient schedule, clinical visit details, and the current uncommitted KIT schedule screen. This was a representative UI review, not an authenticated end-to-end audit of every route. No backend data, credentials or existing user sessions were required.

The root instructions and current working-tree status were checked. Existing changes in the frontend, backend and prompt index belong to the working tree and were not edited by this design task. Some historical instructions mention `sponsor/visit-schedule.tsx`; that file is absent from the current checkout, so the concept is based on the current screens that actually exist.

## Why the existing interface feels basic

The app already has real functionality and a visual system. The issue is the distribution of emphasis:

- Large gradient headers, a portfolio-health ring, four statistic tiles and four action tiles compete before study content on the sponsor dashboard.
- Small uppercase labels and many tinted chips make the hierarchy busy. Several dashboard metadata styles are 7–10 logical pixels.
- Numerous borders, shadows, rounded surfaces and colored icons make minor details feel as prominent as the next action.
- The trial cards repeat creator metadata on the overview, making each card tall. The concept prioritizes trial state and enrollment and leaves attribution for the detailed record.
- Shared clinical and patient workflows need different priorities. Patient home should answer “When is my next visit?” before presenting operational totals.

## Proposed direction

Use a quiet neutral canvas, forest text and teal actions. Preserve Figtree and the recognizable journey-line M mark. Reduce the amount of decorative color and use spacing, type size and alignment to establish hierarchy.

| Token | Proposed value | Use |
| --- | --- | --- |
| Canvas | `#F5F7F4` | Warm neutral background |
| Surface | `#FFFFFF` | Cards, forms and navigation |
| Primary ink | `#193B35` | Headings and main text |
| Secondary ink | `#60716B` | Supporting text |
| Primary action | `#126D5D` | Primary buttons and active navigation |
| Mint | `#E5F3EC` | Selected and supportive surfaces |
| Border | `#E3EAE4` | Quiet structural separators |
| Review | `#895316` on `#FFF3E0` | Items awaiting review |
| Overdue / blocked | `#AC3F39` on `#FBECE9` | Explicit operational problems |
| Scheduled | `#3D5F88` on `#EDF2FA` | Upcoming appointment state |

Typography uses Figtree regular, medium, semibold and bold from the existing repository. Phone titles are generally 27–33 logical pixels, card titles 16–17, supporting text 12–14, and compact metadata 10–11. Proposed mobile spacing uses 22–24 pixel screen gutters, 12–18 pixel card padding and approximately 24 pixel section gaps. Cards have 12–19 pixel corners with thin borders and minimal shadows.

## What each screen changes

| Screen | Intended improvement |
| --- | --- |
| Sponsor overview | Clear action queue, compact totals, trial progress in the first screenful; remove the visually dominant composite-health ring from this concept. |
| Clinical workday | Visits and actionable review items before portfolio totals. Date strip makes the current day explicit. |
| Patient home | Lead with the next appointment and a clear details link; add quiet progress and care-team cards. |
| Trial directory | Standardize metadata order, progress bars and state badges; make search and creation easy to locate. |
| Clinical patient schedule | Separate the next appointment, permitted window and visit timeline. An unresolved anchor is explicitly identified. |
| Clinical visit detail | Preserve clinical tasks, administrative tasks, comments and visit updates in a more legible arrangement. |
| Sign-in | Keep the existing email/phone and password method; establish a cleaner entry screen with visible help. |
| Patient appointment detail | Show the appointment, site and coordinator. Avoid clinical editing controls or invented preparation advice. |
| Desktop sponsor overview | Use the extra width for a sidebar, table and action queue. Treat search shortcuts and reporting as proposed additions. |

Sponsor mobile navigation retains Home, Trials, Messages, Audits and Profile. Study-site access remains visible in the proposed desktop navigation. The board's links are review navigation, not a complete specification of real application routing.

## States and constraints for a future implementation

No implementation is part of this delivery. If the direction is adopted later, preserve the following behavior:

- Missing dates, visit modes, windows, recurrence constraints or anchors must remain unresolved. The displayed schedule values are deliberately fictional fixture values, not defaults to apply to real data.
- Keep clinical tasks, administrative tasks, comments and edit capabilities. Changing visual layout must not change the scheduling source, patient facts, amendments or read modes.
- Show distinct loading, empty, failed, partially loaded, review-required and permission-restricted states. Missing data must not look like a successful zero or a blank card.
- Show schedule-building failures visibly during trial creation. A visual refresh must not preserve or conceal the known silent-fallback issue.
- Keep sponsor access de-identified. Patient views must not acquire clinical editing controls. Capability checks still control real actions.
- Notification icons do not imply that remote push delivery exists. Message contacts do not imply a new communication service.
- Preserve focus behavior, readable contrast, semantic controls, dynamic type and at least 44 × 44 interactive targets during implementation. The static board is not a completed accessibility audit.
- Respect system reduced-motion preferences. Dark mode and additional device sizes are further design work; this delivery establishes a light-mode direction.

## Verification

The local HTML was rendered in Chrome with Playwright, using the bundled fonts. Every mockup was exported to PNG, and the preview was checked for JavaScript errors, missing local assets, horizontal content overflow and content/footer separation. The primary text and status color pairs were measured for contrast. The narrow design-board layout was reviewed separately from the fixed-size phone mockups.

No app tests were run because application files and application behavior were not changed. The files in this folder are a reviewable visual concept, not a production build or an implementation prompt.
