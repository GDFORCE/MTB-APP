# SOS-AMI schedule screenshot compared with the supplied protocol

Reviewed: 24 October 2026.

Source: `D:\AMI Study Protocol (1).pdf`, 129 PDF pages. The title page identifies SOS-AMI, protocol **ID-076A301, Version 6, 21 January 2025**. Page references below use the physical PDF page number, consistent with the document's bottom `n of 129` footer.

## Verdict and limits

The screenshot captures the main visit sequence, but it is **not sufficient to approve the extracted schedule as correct**. The fixed-looking duration, screening/randomization timing, recurrence representation and end condition need correction or explicit review. The Day 0 decision label is a separate, confirmed presentation defect.

This review compares the supplied document with visible screenshot content and relevant local rendering code. The assessment lists, three selected review answers, twelve notes and event detail models are not visible in the screenshot. A read-only search found no saved AMI kit model in the configured local `mtb_local_dev.trial_kit_models` collection at review time. Therefore, this report does not claim to have validated the complete extracted JSON or all procedures. No fresh AI extraction was requested, because that could produce a different model from the one displayed. No application code, clinical policy or trial data was changed.

## What visibly agrees

| Screenshot item | Protocol source | Assessment |
| --- | --- | --- |
| Screening followed by eligibility-dependent randomization | Table 1, p. 32; sections 8.1 and 8.1.1, p. 67 | Broadly appropriate, subject to timing and duplication findings below |
| Week 2 telephone contact with a seven-day window | Table 1, p. 32; section 8.1.2, p. 68 | Correct in principle. **Day 15 is valid study-day numbering:** randomization is Day 1, and two weeks later is Day 15 |
| Month 1 telephone contact with a seven-day window | Table 1, p. 32 | Correct in principle; actual month-based date arithmetic remains to be checked |
| Month 4 and subsequent three-monthly calls with a fourteen-day window | Table 1, p. 32; section 8.1.2, p. 68 | Cadence and windows match the wording; fixed 90-day arithmetic and stopping anchor need review |
| Final site visit within 30 days after global closure announcement | Table 1, p. 32; section 8.1.6, p. 70 | Correct in principle |
| Alternative telephone FSV when treatment stopped and all injectors returned | Section 8.1.6, p. 70 | The displayed decision expresses the relevant condition. Exact branch predicates are not visible |
| Separate treatment discontinuation and study withdrawal event labels | Sections 6.8.1 and 6.8.2, pp. 55–57 | Appropriate distinction. Treatment discontinuation must preserve scheduled study follow-up when participation consent remains |
| Repeatable self-administration and AMI/stroke/TIA event categories | Table 2, p. 33; sections 8.1.3, pp. 68–69 | Appropriate categories; timing and assessment details cannot be verified from an “Adds N visits” badge |

## Visible problems and required corrections

### 1. “10 visits over 19 months” is a preview, not a protocol-defined total

The screenshot includes six occurrences of an open-ended three-monthly series, ending around Month 19. The protocol does not prescribe a fixed ten-visit, nineteen-month course. Section 5.2 and p. 48 describe variable individual observation duration and an **estimated median of 18 months**. Section 5.3 ties global closure announcement to the study-level treated-subject target. Regular calls continue toward each subject's FSV.

The header should explicitly describe a preview, such as “Scheduled contacts continue until final study visit; showing six recurring contacts.” It should distinguish known scheduled contacts, open-ended occurrences and final visits waiting for an actual date. The site and telephone FSV rows are alternatives, not two mandatory visits for one participant.

### 2. Screening consent is not an unconditional same-day randomization anchor

The screenshot uses “Fully signed screening consent” as its start and displays both SCR and D1 on Day 1 with exact-day windows. Table 1 footnote 6 and section 8.1 state that screening may take several days. Randomization must be no later than **28 days after diagnosis of the qualifying type 1 AMI**, not 28 days after consent.

Same-day screening/randomization is possible, so the shared date is not inherently wrong for a particular participant. It must not be the only supported protocol interpretation. The model needs the actual randomization date to anchor Week 2, Month 1 and later contacts, plus the qualifying AMI diagnosis date to validate the randomization deadline. Screening precedes randomization or screening failure.

### 3. The recurring-call end condition is earlier than the stated follow-up endpoint

The screenshot's repeat says “Until Global study closure announcement.” The protocol describes regular contacts up to FSV (pp. 22 and 47), and section 8.1.6 allows FSV within 30 days after the announcement. Those dates are not interchangeable.

The model must not silently omit a regular contact falling between closure announcement and the individual's FSV. Any intended consolidation or stopping rule needs protocol evidence or explicit review.

### 4. “Every 90 days” substitutes a fixed interval for month-based wording

The protocol says Month 1, Month 4 and every three months thereafter. The screenshot says every 90 days. The reviewed scheduling sections do not define a month as 30 days. Over a long study, those representations can diverge.

Preserve month-based timing or require an explicit documented resolution of the conversion. A site review answer allowing a finite preview does not establish that month-to-day conversion is correct.

### 5. “Day 1 – Day 0” is a renderer defect

The decision at SCR appears on Day 0 while the visit on the same date appears on Day 1. `frontend/src/features/kit/sections.ts` renders visits with their study-day value but decision rows with their raw offset from the stand-in start date. Calling the actual helper confirms `relLabel(0, 1) = "Day 1"` and `relLabel(0) = "Day 0"`.

Use one study-day convention for both visits and decisions. This is a presentation defect, not evidence that the protocol contains an additional Day 0 visit.

### 6. Screening and randomization may be double-counted

Table 1 has one Visit 1 screening/randomization column; the app displays separate SCR and D1 rows, each reporting twelve assessments. Splitting screening and randomization into operational stages can be valid, but it does not authorize repeating the full procedure list or counting two required site visits for every participant.

Identical assessment counts do not prove the hidden lists are identical. Check the underlying model to confirm that procedures are appropriately allocated, not copied twice, and that the total visit count explains the distinction between a protocol visit and operational stages.

## Details that must be checked in the hidden model

### Routine and final assessments — Table 1, p. 32

- Routine calls include training recollection, concomitant antithrombotic medication reporting and AE/SAE reporting under their protocol-specific rules.
- EQ-5D is country-limited. Within the recurring calls it is marked at **Visit 4 / Month 4 and Visit 7 / Month 13**, not every recurring call. Table 1 also includes its baseline rule. The listed countries are USA, Germany, France, Netherlands, Italy, Finland, Australia, New Zealand, Canada, UK, Sweden and Denmark.
- Screening pregnancy testing is conditional on childbearing potential. At FSV it additionally depends on study-treatment administration or uncertainty about administration.
- Frailty characteristics apply only to subjects aged 65 or older.
- The FSV assessment column differs from the screening and routine-call columns. Counts alone do not validate the mapping.

The screenshot's “3 assessments” on recurring calls does not by itself prove EQ-5D is missing: the answer to the hidden site-country question could correctly exclude it. Conversely, it does not demonstrate that the occurrence-specific rule is implemented.

### Event-triggered assessments — Table 2, p. 33; section 8.1.3, pp. 68–69

- Following each qualifying study-treatment self-administration, the post-treatment assessment visit is within two days. Footnote 8 stresses immediate medical attendance, with activities possibly spanning several days. A scheduling system must not turn this into advice to wait two days.
- The subsequent follow-up contact is **30–37 days after self-administration**, not an unrestricted ±7-day window around Day 30.
- Post-treatment assessments repeat for every self-administration except the supervised screening placebo administration.
- When a second self-administration occurs less than 30 days after the first, the two follow-up contacts may be combined, using the **second administration's 30–37-day window**.
- A routine telephone call may be combined with a post-treatment contact only if the combined contact meets the 30–37-day requirement.
- AMI or stroke/TIA without study-treatment administration still triggers the required assessment workflow. Table 2 footnote 10 uses **first medical contact** as the time reference in that circumstance. Section 6.8.1 also discusses event-onset reference after premature treatment discontinuation; retain and explicitly resolve the context rather than silently choosing one anchor for all situations.
- Death ends routine participant scheduling, but Table 2 footnote 10 still requires the relevant event-assessment documentation. “Ends schedule” alone does not establish preservation of that workflow; the badge also hides any visits attached to an ending event.

### Withdrawal, pregnancy and rescreening — pp. 54–57 and 70

- Premature discontinuation of the treatment option does not withdraw a consenting subject from study follow-up. Table 1 activities continue, and FSV remains required.
- Confirmed pregnancy requires permanent discontinuation of the treatment option. Where the protocol's prior-exposure condition applies, pregnancy follow-up continues to its conclusion. A “Recorded” event badge does not establish that these consequences are modeled.
- For study withdrawal other than death or loss to follow-up, the investigator should attempt a last contact, with consent-dependent handling. Section 8.1.6 records an agreed final withdrawal contact as FSV.
- A lost-to-follow-up declaration follows the contact-attempt requirements and at least 90 days between first and last unsuccessful attempts. It must not be inferred from a single missed call.
- A screening failure may be rescreened after a new AMI, with fresh consent and all screening assessments repeated. Repeating eligibility assessments within the same screening attempt is not allowed.

## Existing engine limitations relevant to AMI

These are separate from whether the extracted model contains the right rules:

- The earlier kit coverage review confirmed that event visit keys generated by the frontend are rejected when actual completion dates are saved by the backend validator. Correctly displaying the AMI event categories does not resolve that defect.
- A finite `open_ended` repeat does not automatically extend after all preview occurrences are completed. Six displayed quarterly contacts cannot be the permanent limit for this study.
- Event combination rules recorded only as notes are not automatically enforced by the current arithmetic engine.
- The detailed timeline marks unknown conditions, but simplified patient visit rows can lose that conditional status.

## Acceptance checks before using this extracted schedule

1. A participant screened and randomized on different dates gets follow-up dates relative to actual randomization; an out-of-bound AMI-to-randomization interval is identified.
2. A participant still active after Month 19 continues receiving the quarterly series.
3. A regular contact between closure announcement and FSV is preserved or explicitly handled under a reviewed rule.
4. Month 4 and Month 13 carry EQ-5D for applicable countries; other recurring calls do not acquire it by default.
5. Event contacts use the correct 0–2 and 30–37-day intervals and the correct source date, including repeat self-administrations and combination scenarios.
6. Treatment discontinuation preserves study follow-up, whereas consent withdrawal, death and lost-to-follow-up follow their distinct closure/documentation rules.
7. The final site/telephone branches are mutually exclusive and use the actual qualifying conditions.
8. An event-triggered actual date can be saved, and an exhausted preview cannot mark an ongoing participant completed.

The current screenshot establishes partial extraction coverage. It does not establish that these acceptance checks pass.
