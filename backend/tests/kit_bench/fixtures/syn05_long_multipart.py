"""SYN-05: a longer two-part protocol (double-blind part, early escape, open-label extension) with
the schedule spread over a two-page table with tick marks and numbered footnotes, a second table
for the extension, scheduling statements scattered through the text, and one table/text conflict.

Constructed fixture, not a real protocol. Style: mid-size European sponsor; most of the document
is ordinary protocol content that says nothing about the schedule.

Rules it carries:
- Screening Day -35 to Day -1; Baseline = Day 1 = Week 0.
- Part 1 visits at Weeks 2, 4 (+/-3 days), 8, 12, 16, 20, 24 (+/-5 days), counted from Baseline.
  Injections at the site at Weeks 0, 2, 4, 8, 12, 16, 20.
- Week 16: non-responders enter early escape: hand/foot radiographs at Week 16 and an extra
  safety visit at Week 18 (+/-3 days).
- Fasting lipids (8 h fast) at Baseline, Week 12, Week 24. PK/ADA at Weeks 0, 4, 12, 24, 48, 72.
- Chest X-ray at screening unless one from the previous 3 months is available.
- ECG: the table marks Screening, Week 12 and Week 24; the text says Screening, Week 24 and Week 72.
- Week 24 decision: enter the open-label extension (OLE) or not.
  OLE: Week 28 (+/-5), Weeks 36, 48, 60, 72 (+/-7), safety follow-up at Week 76 (+/-7).
  Not entering: safety follow-up 8 weeks after the last dose (+/-7).
- Study drug stopped early in Part 1: early-termination visit within 14 days of the decision,
  the remaining Part 1 visits are still attended without injections, no OLE, safety follow-up
  8 weeks after the last dose.
- Withdrawal of consent: nothing further.
- ALT or AST above 3 x ULN: repeat liver tests within 48 to 72 hours, then weekly until below 2 x ULN.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, event, has, model, proc, question, repeat, rule, visit, win
from ..pdfgen import Pdf

ID = "syn05-long-multipart"
TITLE = "ARX-3301 LUMEN-2: double-blind part, early escape and open-label extension (two-page tick-mark table)"
COVERS = ["schedule table continued over two pages", "tick marks from a symbol font", "numbered footnotes", "second table for the extension",
          "scheduling statements scattered in a long document", "table and text disagree", "window changes by period",
          "decision at a visit adds visits (early escape)", "decision at a visit opens an extension", "visit after last dose",
          "treatment stopped but visits continue", "early termination visit", "event-triggered visits repeated until a result recovers",
          "reusable earlier result", "fasting preparation", "withdrawal of consent"]

SCR, B = "2025-12-15", "2026-01-12"
WK = lambda n: d(B, 7 * n)  # noqa: E731

FILLER = {
    "2. INTRODUCTION AND RATIONALE": [
        "Psoriatic arthritis (PsA) is a chronic inflammatory arthropathy associated with psoriasis. Despite the availability of "
        "conventional and biologic disease-modifying antirheumatic drugs, a substantial proportion of patients do not achieve "
        "sustained low disease activity. Arelixumab (ARX-3301) is a humanised monoclonal antibody directed against the p19 subunit of "
        "interleukin-23. In the phase 2 dose-ranging study, arelixumab 150 mg produced clinically meaningful improvements in joint "
        "and skin outcomes with an acceptable safety profile.",
        "The dose regimen selected for this study is supported by population pharmacokinetic and exposure-response modelling of the "
        "phase 2 data. The half-life of arelixumab is approximately 25 days, which supports dosing every 4 weeks after an initial "
        "loading phase.",
        "The benefit-risk assessment for participation in this study is considered favourable. The principal identified risks are "
        "injection-site reactions and upper respiratory tract infections; important potential risks include serious infections and "
        "hepatic enzyme elevations. Risk-minimisation measures are described in the relevant sections of this protocol."],
    "3. OBJECTIVES AND ENDPOINTS": [
        "The primary objective is to demonstrate the superiority of arelixumab over placebo in the proportion of patients achieving "
        "an ACR20 response. The key secondary objectives are to evaluate the effect of arelixumab on physical function, on skin "
        "disease in patients with at least 3% body surface area involvement, on enthesitis and dactylitis, and on the inhibition of "
        "structural damage progression.",
        "Exploratory objectives include the evaluation of patient-reported fatigue, of work productivity, and of the pharmacokinetics "
        "and immunogenicity of arelixumab. Long-term safety and maintenance of response are evaluated in the open-label extension."],
    "5. STUDY POPULATION": [
        "Inclusion criteria. Patients must meet all of the following: (1) age 18 to 75 years inclusive at the time of consent; (2) a "
        "diagnosis of PsA for at least 6 months fulfilling the CASPAR classification criteria; (3) at least 3 tender and at least 3 "
        "swollen joints at Screening and at Baseline; (4) at least one psoriatic plaque of 2 cm or more, or nail changes "
        "characteristic of psoriasis, or a documented history of plaque psoriasis; (5) inadequate response or intolerance to at least "
        "one conventional synthetic DMARD; (6) if receiving methotrexate, a stable dose not exceeding 25 mg per week for at least 8 "
        "weeks; (7) women of childbearing potential must use highly effective contraception from consent until 20 weeks after the "
        "last dose of study drug.",
        "Exclusion criteria. Patients meeting any of the following are not eligible: (1) previous exposure to any agent targeting "
        "interleukin-23 or interleukin-12/23; (2) more than two previous tumour necrosis factor inhibitors; (3) active or untreated "
        "latent tuberculosis; (4) a serious infection requiring hospitalisation within 8 weeks before Baseline; (5) hepatitis B "
        "surface antigen positive, hepatitis C viraemia or known HIV infection; (6) alanine aminotransferase or aspartate "
        "aminotransferase above 2 times the upper limit of normal at Screening; (7) a history of malignancy within 5 years other than "
        "adequately treated non-melanoma skin cancer or cervical carcinoma in situ; (8) pregnancy or breast-feeding; (9) receipt of a "
        "live vaccine within 6 weeks before Baseline.",
        "Lifestyle considerations. Live vaccines must not be given during the study and for 20 weeks after the last dose. No other "
        "restrictions on diet or activity apply, except for the fasting requirement before lipid sampling."],
    "10. STATISTICAL CONSIDERATIONS": [
        "Sample size. Assuming ACR20 response rates of 55% for arelixumab and 30% for placebo at the primary time point, 220 patients "
        "per group provide more than 95% power at a two-sided significance level of 0.05. Allowing for stratification and a small "
        "proportion of non-evaluable patients, approximately 450 patients will be randomised.",
        "Analysis sets. The full analysis set comprises all randomised patients who receive at least one dose of study drug. The "
        "safety set comprises all patients who receive at least one dose of study drug, analysed according to treatment received.",
        "Estimands. The primary estimand uses a composite strategy for treatment discontinuation and early escape: patients who "
        "discontinue study drug or enter early escape before the primary time point are considered non-responders. A supplementary "
        "estimand uses a treatment-policy strategy, for which data collected after discontinuation of study drug are used.",
        "Multiplicity is controlled by a fixed-sequence testing procedure across the primary and key secondary endpoints. Missing "
        "binary data are imputed as non-response; continuous endpoints are analysed with a mixed model for repeated measures."],
    "11. ETHICS, DATA HANDLING AND ADMINISTRATION": [
        "The study is conducted in accordance with the Declaration of Helsinki, ICH E6 Good Clinical Practice and applicable "
        "regulations. The protocol, the informed consent form and any patient-facing materials are approved by the responsible "
        "ethics committee before any patient is enrolled.",
        "Data are recorded in an electronic case report form. The investigator retains essential documents for at least 25 years "
        "after the end of the study. An independent data monitoring committee reviews unblinded safety data at regular intervals."],
}


def build_pdf(path: str) -> None:
    pdf = Pdf(header="Arelix Pharma GmbH | ARX-3301-302 (LUMEN-2) | Protocol v4.0 incl. Amendment 3 | 19-Dec-2025",
              footer="Company confidential", font="helv", bold="hebo", size=9.5)
    pdf.title(["ARX-3301-302 (LUMEN-2)", "A Phase 3, Multicentre, Randomised, Double-Blind, Placebo-Controlled Study of Arelixumab in "
               "Adults With Active Psoriatic Arthritis, With an Open-Label Extension",
               "Sponsor: Arelix Pharma GmbH", "Protocol version 4.0 incorporating Amendment 3, 19 December 2025", "EU CT number: (constructed fixture)"])
    pdf.new_page()
    pdf.h1("1. SYNOPSIS")
    pdf.p("Design. The study has two parts. Part 1 is a 24-week randomised, double-blind, placebo-controlled treatment period. Part 2 "
          "is an open-label extension (OLE) in which all patients receive arelixumab up to Week 72. Patients are randomised 1:1 at "
          "Baseline to arelixumab 150 mg or placebo, given by subcutaneous injection.")
    pdf.p("Screening lasts up to 35 days (Day -35 to Day -1). Baseline is Day 1 (Week 0). All visits in Part 1 and Part 2 are "
          "scheduled from the Baseline date.")
    pdf.p("Early escape. At Week 16, patients with less than 20% improvement from Baseline in both tender and swollen joint counts "
          "are classified as non-responders and enter early escape (see Section 4.3).")
    for section in ("2. INTRODUCTION AND RATIONALE", "3. OBJECTIVES AND ENDPOINTS"):
        pdf.h1(section)
        for para in FILLER[section]:
            pdf.p(para)
    pdf.h1("4. STUDY DESIGN")
    pdf.h2("4.1 Part 1: double-blind treatment period (Baseline to Week 24)")
    pdf.p("Study drug is administered at the study site by subcutaneous injection at Baseline (Week 0), Week 2 and Week 4, and "
          "every 4 weeks thereafter up to and including Week 20. No study drug is administered at the Week 24 visit in Part 1; "
          "patients who enter the OLE receive their first open-label dose at Week 24 (Section 4.4).")
    pdf.h2("4.2 Visit windows")
    pdf.p("Visits up to and including Week 4 are to take place within 3 days before or after the scheduled day. From Week 8 to "
          "Week 28 the window is 5 days before or after. From Week 36 onwards, and for safety follow-up visits, the window is 7 days "
          "before or after. Windows do not accumulate: each visit is scheduled from Baseline irrespective of the actual date of "
          "the previous visit.")
    pdf.h2("4.3 Early escape at Week 16")
    pdf.p("Non-responders at Week 16 (Section 1) enter early escape. Radiographs of the hands and feet are obtained at the Week 16 "
          "visit in these patients only. Early-escape patients attend one additional safety visit at Week 18 (window 3 days before "
          "or after), at which vital signs, haematology, chemistry and adverse events are assessed. All other visits are unchanged.")
    pdf.h2("4.4 Part 2: open-label extension")
    pdf.p("At the Week 24 visit the investigator and patient decide whether the patient enters the OLE. Patients who have "
          "permanently discontinued study drug during Part 1 are not eligible for the OLE. In the OLE, arelixumab is administered "
          "at the site at Week 24 and Week 28 and is then self-administered at home every 4 weeks, with the last dose at Week 68. "
          "OLE visits are listed in Table 2.")
    pdf.h2("4.5 Safety follow-up")
    pdf.p("Patients who do not enter the OLE attend a safety follow-up visit 8 weeks after their last dose of study drug. "
          "Patients who complete the OLE attend the safety follow-up visit at Week 76, that is 8 weeks after the last dose at "
          "Week 68. Patients who discontinue study drug early attend the safety follow-up visit 8 weeks after their last dose.")
    pdf.h1("5. STUDY POPULATION")
    for para in FILLER["5. STUDY POPULATION"]:
        pdf.p(para)
    pdf.h1("6. SCHEDULE OF ASSESSMENTS")
    pdf.p("The assessments in Part 1 are shown in Table 1 and those in the open-label extension in Table 2.")
    pdf.new_page(landscape=True)
    T = "4"  # the ZapfDingbats tick
    head = [["Assessment", "Screening", "Baseline", "W2", "W4", "W8", "W12", "W16", "W20", "W24"],
            ["Study day", "-35 to -1", "1", "15", "29", "57", "85", "113", "141", "169"]]
    body = [
        ["Informed consent", T, "", "", "", "", "", "", "", ""],
        ["Inclusion / exclusion criteria", T, T, "", "", "", "", "", "", ""],
        ["Medical history, demographics", T, "", "", "", "", "", "", "", ""],
        ["Physical examination", T, T, "", "", "", T, "", "", T],
        ["Vital signs", T, T, T, T, T, T, T, T, T],
        ["Body weight", T, T, "", "", "", T, "", "", T],
        ["12-lead ECG", T, "", "", "", "", T, "", "", T],
        ["Chest X-ray 1", T, "", "", "", "", "", "", "", ""],
        ["Tuberculosis test (IGRA)", T, "", "", "", "", "", "", "", ""],
        ["Hepatitis B, hepatitis C and HIV serology", T, "", "", "", "", "", "", "", ""],
        ["Pregnancy test 2", T, T, "", T, T, T, T, T, T],
        ["Haematology, chemistry", T, T, T, T, T, T, T, T, T],
        ["Fasting lipid panel 3", "", T, "", "", "", T, "", "", T],
        ["hs-CRP", T, T, "", T, T, T, T, T, T],
        ["Tender and swollen joint counts (68/66)", T, T, T, T, T, T, T, T, T],
        ["PASI and body surface area", "", T, "", T, "", T, T, "", T],
        ["Enthesitis and dactylitis assessment", "", T, "", T, "", T, T, "", T],
        ["HAQ-DI, patient global, pain VAS", T, T, T, T, T, T, T, T, T],
        ["SF-36, FACIT-Fatigue", "", T, "", "", "", T, "", "", T],
        ["Radiographs of hands and feet 4", "", T, "", "", "", "", "", "", T],
        ["Responder assessment for early escape", "", "", "", "", "", "", T, "", ""],
        ["PK and anti-drug antibody sample 5", "", T, "", T, "", T, "", "", T],
        ["Randomisation", "", T, "", "", "", "", "", "", ""],
        ["Study drug injection at site 6", "", T, T, T, T, T, T, T, ""],
        ["Adverse events, concomitant medication", T, T, T, T, T, T, T, T, T],
    ]
    widths = [215, 62, 58, 46, 46, 46, 46, 46, 46, 46]
    pdf.text("Table 1  Schedule of assessments: Part 1 (double-blind period)", font="hebo", size=10)
    pdf.table(head + body[:13], widths, header_rows=2, size=7.5, mark_font="zadb", marks=(T,))
    pdf.new_page(landscape=True)
    pdf.text("Table 1  (continued)", font="hebo", size=10)
    pdf.table(body[13:], widths, header_rows=0, size=7.5, mark_font="zadb", marks=(T,))
    for note in (
        "1  Not required if a chest X-ray performed within 3 months before Screening is available.",
        "2  Women of childbearing potential only: serum test at Screening, urine test at subsequent visits.",
        "3  After a fast of at least 8 hours.",
        "4  In early-escape patients, radiographs are also obtained at Week 16 (Section 4.3).",
        "5  Sample taken before the study drug injection.",
        "6  Not administered after permanent discontinuation of study drug (Section 8.2).",
    ):
        pdf.text(note, size=7.5, gap=1)
    pdf.y += 8
    pdf.text("Table 2  Schedule of assessments: Part 2 (open-label extension)", font="hebo", size=10)
    ole = [["Assessment", "W28", "W36", "W48", "W60", "W72 / end of OLE", "Safety follow-up W76 7"],
           ["Vital signs", T, T, T, T, T, T],
           ["Physical examination", "", "", T, "", T, T],
           ["Pregnancy test 2", T, T, T, T, T, T],
           ["Haematology, chemistry", T, T, T, T, T, T],
           ["Tuberculosis test (IGRA)", "", "", T, "", "", ""],
           ["Tender and swollen joint counts (68/66)", T, T, T, T, T, ""],
           ["HAQ-DI, patient global, pain VAS", T, T, T, T, T, ""],
           ["Radiographs of hands and feet", "", "", "", "", T, ""],
           ["PK and anti-drug antibody sample", "", "", T, "", T, ""],
           ["Study drug injection at site", T, "", "", "", "", ""],
           ["Dispense study drug for home use", T, T, T, T, "", ""],
           ["Adverse events, concomitant medication", T, T, T, T, T, T]]
    pdf.table(ole, [215, 60, 60, 60, 60, 90, 110], header_rows=1, size=7.5, mark_font="zadb", marks=(T,))
    pdf.text("7  For patients who do not enter the OLE or who discontinue study drug early, the safety follow-up visit takes place "
             "8 weeks after the last dose of study drug (Section 4.5).", size=7.5, gap=1)
    pdf.new_page()
    pdf.h1("7. STUDY ASSESSMENTS")
    pdf.h2("7.1 Efficacy")
    pdf.p("Joint counts are performed by an independent assessor who is blinded to all other study data. Patient-reported outcomes "
          "are completed before any other assessment at the visit.")
    pdf.h2("7.2 Electrocardiogram")
    pdf.p("A 12-lead ECG is recorded after the patient has rested supine for at least 5 minutes. ECGs are recorded at Screening, "
          "at Week 24 and at Week 72.")
    pdf.h2("7.3 Laboratory assessments")
    pdf.p("Blood for the fasting lipid panel is drawn after a fast of at least 8 hours; patients are reminded at the previous "
          "visit. All other laboratory samples may be taken without fasting.")
    pdf.h2("7.4 Hepatic monitoring")
    pdf.p("If alanine aminotransferase (ALT) or aspartate aminotransferase (AST) exceeds 3 times the upper limit of normal (ULN) "
          "at any time, liver function tests are repeated within 48 to 72 hours of the result becoming known. Liver function tests "
          "are then repeated weekly until ALT and AST are both below 2 times ULN.")
    pdf.h1("8. DISCONTINUATION")
    pdf.h2("8.1 General")
    pdf.p("A distinction is made between permanent discontinuation of study drug and withdrawal from the study.")
    pdf.h2("8.2 Permanent discontinuation of study drug")
    pdf.p("Patients who permanently discontinue study drug attend an early termination (ET) visit within 14 days of the decision "
          "to discontinue, at which the assessments of the Week 24 visit are performed. Patients who discontinue study drug during "
          "Part 1 are asked to continue to attend all remaining scheduled Part 1 visits up to Week 24 without receiving study "
          "drug, so that data are available for the treatment-policy estimand; they do not enter the OLE. Patients who discontinue "
          "study drug during the OLE do not attend further OLE visits after the ET visit. In all cases the safety follow-up visit "
          "takes place 8 weeks after the last dose of study drug (Section 4.5).")
    pdf.h2("8.3 Withdrawal of consent")
    pdf.p("A patient may withdraw from the study at any time. After withdrawal of consent no further visits or assessments take "
          "place and no further data are collected.")
    pdf.h1("9. SAFETY REPORTING")
    pdf.p("All serious adverse events are reported to the sponsor within 24 hours of the investigator becoming aware of them. "
          "Pregnancy in a patient is reported within 24 hours; study drug is permanently discontinued (Section 8.2) and the "
          "pregnancy is followed to outcome.")
    for section in ("10. STATISTICAL CONSIDERATIONS", "11. ETHICS, DATA HANDLING AND ADMINISTRATION"):
        pdf.h1(section)
        for para in FILLER[section]:
            pdf.p(para)
    pdf.save(path)


DATES = [{"what": ["screening", "consent"], "date": SCR}, {"what": ["baseline", "day 1", "week 0", "randomi*"], "date": B}]
WOCBP = lambda yes: {"what": ["childbearing", "wocbp"], "answer": yes}  # noqa: E731
XRAY = {"what": ["chest x ray", "x ray"], "answer": False, "optional": True}
ESCAPE_WORDS = ["escape", "non respon*", "nonrespon*", "less than 20"]
# The model may ask "responder?" or "early escape?": the two have opposite answers.
RESP = lambda yes: [{"what": ["respon*", "improvement"], "unless": ESCAPE_WORDS, "answer": yes, "optional": True},  # noqa: E731
                    {"what": ESCAPE_WORDS, "answer": not yes, "optional": True}]
OLE = lambda yes: {"what": ["ole", "open label", "extension"], "answer": yes}  # noqa: E731

wk = lambda n: [f"week {n}", f"w{n}", f"wk {n}", f"wk{n}"]  # noqa: E731
VITALS, ECG, LIPIDS = has("Vital signs", "vital*"), has("12-lead ECG", "ecg"), has("Fasting lipid panel", "lipid*")
PKADA, INJ = has("PK / ADA sample", "pk", "anti drug", "ada"), has("Study drug injection", "injection", "study drug administ*", "arelixumab")
XR, JOINTS = has("Radiographs of hands and feet", "radiograph*"), has("Joint counts", "joint count*")
PREG, IGRA = has("Pregnancy test", "pregnancy"), has("Tuberculosis test", "tuberculosis", "igra", "tb")
SFU = [["safety follow up"], ["safety followup"], ["sfu"], ["follow up"]]
ET = [["early termination"], ["et"]]
STOP = [["discontinu*", "study drug"], ["study drug", "discontinu*"], ["discontinu*", "treatment"], ["treatment", "discontinu*"]]
WINDOW = {2: 3, 4: 3, 8: 5, 12: 5, 16: 5, 20: 5, 24: 5, 28: 5, 36: 7, 48: 7, 60: 7, 72: 7, 76: 7}


def part1(wocbp: bool, escape: bool, dosing: bool = True) -> list[dict[str, Any]]:
    inj = [0, 2, 4, 8, 12, 16, 20] if dosing else []
    rows = [ev("Baseline", ["baseline", "day 1", "week 0", "w0"], B, has=[VITALS, LIPIDS, PKADA, XR, JOINTS, INJ, *([PREG] if wocbp else [])],
               lacks=[ECG, IGRA, *([] if wocbp else [PREG])])]
    for n in (2, 4, 8, 12, 16, 20, 24):
        yes = [VITALS, JOINTS]
        no: list[dict[str, Any]] = [IGRA]
        (yes if n in (12, 24) else no).append(LIPIDS)
        (yes if n in (4, 12, 24) else no).append(PKADA)
        (yes if n in inj else no).append(INJ)
        (yes if n == 24 or (n == 16 and escape) else no).append(XR)
        if n == 2:
            no.append(PREG)
        elif wocbp:
            yes.append(PREG)
        rows.append(ev(f"Week {n}", wk(n), WK(n), win(WK(n), WINDOW[n], WINDOW[n]), has=yes, lacks=no))
    return rows


def expected() -> dict[str, Any]:
    ole = [ev(f"Week {n} (OLE)", wk(n), WK(n), win(WK(n), WINDOW[n], WINDOW[n]), has=[VITALS, *([IGRA, PKADA] if n == 48 else []), *([XR, PKADA] if n == 72 else [])],
              lacks=[*([] if n in (48,) else [IGRA]), *([] if n == 72 else [XR])]) for n in (28, 36, 48, 60, 72)]
    conflict = {"name": "ECG: Table 1 marks Screening, Week 12 and Week 24; Section 7.2 says Screening, Week 24 and Week 72",
                "any": [["ecg", "week 12"], ["ecg", "week 72"], ["ecg", "table", "section"], ["ecg", "conflict*"], ["ecg", "differ*"], ["ecg", "disagree*"]]}
    return {"fixture": ID, "constructed": True, "cases": [
        {"name": "A: responder at Week 16, enters the OLE (woman of childbearing potential)",
         "given": {"today": SCR, "dates": DATES, "facts": [WOCBP(True), XRAY, *RESP(True), OLE(True)]},
         "expect": {"visits": [
             ev("Screening", ["screening", "scr"], SCR, has=[VITALS, ECG, IGRA, has("Serum pregnancy test", ["serum", "pregnancy"]), has("Chest X-ray", "chest x ray", "chest")],
                lacks=[LIPIDS, INJ, XR]),
             *part1(True, False), *ole,
             ev("Safety follow-up at Week 76 (8 weeks after the last dose at Week 68)", SFU, WK(76), win(WK(76), 7, 7), has=[VITALS], lacks=[JOINTS, INJ])],
             "absent": [{"name": "Week 18 early-escape visit (responder)", "match": wk(18)}],
             "flags": [conflict]}},
        {"name": "B: non-responder at Week 16: early escape (radiographs at Week 16, extra visit at Week 18)",
         "given": {"today": SCR, "dates": DATES, "facts": [WOCBP(False), XRAY, *RESP(False), OLE(True)]},
         "expect": {"visits": [*part1(False, True), ev("Week 18 early-escape safety visit", wk(18), WK(18), win(WK(18), 3, 3), has=[VITALS], lacks=[JOINTS, INJ])]}},
        {"name": "C: completes Part 1, does not enter the OLE: safety follow-up 8 weeks after the Week 20 dose",
         "given": {"today": SCR, "dates": DATES, "facts": [WOCBP(False), XRAY, *RESP(True), OLE(False)]},
         "expect": {"visits": [ev("Week 24", wk(24), WK(24)), ev("Safety follow-up (8 weeks after the last dose at Week 20)", SFU, WK(28), win(WK(28), 7, 7), has=[VITALS], lacks=[INJ])],
                    "absent": [{"name": "OLE Week 36", "match": wk(36)}, {"name": "OLE Week 72", "match": wk(72)}]}},
        {"name": "D: study drug stopped for good on 23 Feb (last injection Week 4): ET visit, Part 1 visits continue without injections, no OLE",
         "given": {"today": d(WK(4), 14), "dates": DATES, "facts": [WOCBP(False), XRAY],
                   "actuals": [{"visit": ["baseline", "day 1", "week 0", "w0"], "date": B, "all": True}, {"visit": wk(2), "date": WK(2), "all": True},
                               {"visit": wk(4), "date": WK(4), "all": True}],
                   "events": [{"what": STOP, "date": d(WK(4), 14)}]},
         "expect": {"visits": [
             ev("Early termination visit (within 14 days of the decision)", ET, date_in=[d(WK(4), 14), d(WK(4), 28)], has=[VITALS, ECG, XR], lacks=[INJ]),
             ev("Week 8 (still attended, no injection)", wk(8), WK(8), has=[VITALS, JOINTS], lacks=[INJ]),
             ev("Week 16 (still attended, no injection)", wk(16), WK(16), has=[VITALS], lacks=[INJ]),
             ev("Week 24 (still attended)", wk(24), WK(24), has=[VITALS]),
             ev("Safety follow-up 8 weeks after the last dose (Week 4 + 8 weeks)", SFU, WK(12), win(WK(12), 7, 7))],
             "absent": [{"name": "OLE Week 28", "match": wk(28)}, {"name": "OLE Week 48", "match": wk(48)}]}},
        {"name": "E: consent withdrawn at Week 10: nothing afterwards",
         "given": {"today": WK(10), "dates": DATES, "facts": [WOCBP(False), XRAY], "events": [{"what": [["withdraw*"]], "date": WK(10)}]},
         "expect": {"visits": [ev("Week 8", wk(8), WK(8))],
                    "absent": [{"name": "Week 12", "match": wk(12), "after": WK(10)}, {"name": "Week 24", "match": wk(24), "after": WK(10)},
                               {"name": "Safety follow-up", "match": SFU, "after": WK(10)}, {"name": "Early termination visit", "match": ET, "after": WK(10)}]}},
        {"name": "F: ALT above 3 x ULN known on 20 Mar: liver tests within 48 to 72 hours, then weekly until below 2 x ULN",
         "given": {"today": "2026-03-20", "dates": DATES, "facts": [WOCBP(False), XRAY],
                   "events": [{"what": [["alt"], ["liver"], ["hepatic"], ["transaminase*"], ["aminotransferase*"]], "date": "2026-03-20"}]},
         "expect": {"visits": [
             ev("Repeat liver function tests within 48 to 72 hours", [["liver"], ["lft"], ["hepatic"], ["alt"]], date_in=["2026-03-22", "2026-03-23"],
                has=[has("Liver function tests", "liver", "lft", "alt", "hepatic")]),
             ev("Weekly repeat liver function tests", [["liver"], ["lft"], ["hepatic"], ["alt"]], date_in=["2026-03-29", "2026-03-30"])],
             "visits_note": "weekly repeats count from the first repeat test"}},
    ]}


def hand_model() -> dict[str, Any]:
    wocbp = {"question": "wocbp", "in": ["yes"]}
    escape = {"question": "responder_w16", "in": ["no"]}
    ole_yes, ole_no = {"question": "enter_ole", "in": ["yes"]}, {"question": "enter_ole", "in": ["no"]}
    stopped = {"event": "stop_drug", "happened": True}
    common = ["Vital signs", "Haematology, chemistry", "Tender and swollen joint counts (68/66)", "HAQ-DI, patient global, pain VAS",
              "Adverse events, concomitant medication"]
    # Injections are their own rows after each visit's assessments, so stopping study drug can stop
    # them while the visits go on, and "the last dose" is the last injection actually given.
    doses = [0, 2, 4, 8, 12, 16, 20]
    preg = proc("Urine pregnancy test", wocbp)
    w24 = ["Physical examination", "Body weight", "12-lead ECG", preg, proc("Fasting lipid panel", need="required"), "hs-CRP", "PASI and body surface area",
           "Enthesitis and dactylitis assessment", "SF-36, FACIT-Fatigue", "Radiographs of hands and feet", "PK and anti-drug antibody sample", *common]

    def p1(n: int, extra: list[Any], dose: bool = True) -> dict[str, Any]:
        return visit(f"w{n}", f"W{n}", f"Week {n}", "Part 1", "baseline", 7 * n, (WINDOW[n], WINDOW[n]), [*extra, *common],
                     **({"prep": {"fasting_hours": 8, "notes": []}} if n in (12, 24) else {}))

    def ole(n: int, extra: list[Any]) -> dict[str, Any]:
        return visit(f"w{n}", f"W{n}", f"Week {n} (OLE)", "Open-label extension", "baseline", 7 * n, (WINDOW[n], WINDOW[n]),
                     ["Vital signs", preg, "Haematology, chemistry", "Tender and swollen joint counts (68/66)", "HAQ-DI, patient global, pain VAS",
                      *extra, "Adverse events, concomitant medication"], include_if=[ole_yes])

    return model(TITLE, "ARX-3301-302",
        anchors=[
            anchor("screening", "start", "Screening"),
            anchor("baseline", "ask", "Baseline (Day 1)", is_day1=True, set_by_visit="w0", question="Baseline date?",
                   range={"relative_to": "screening", "min_days": 1, "max_days": 35}, estimates=[{"when": [], "relative_to": "screening", "offset_days": 28}]),
            anchor("last_dose", "last_visit", "Last dose of study drug", last_of=[f"dose{n}" for n in doses]),
        ],
        questions=[
            question("wocbp", "Childbearing potential", "Woman of childbearing potential?"),
            question("recent_cxr", "Recent chest X-ray", "Chest X-ray from the last 3 months available?"),
            question("responder_w16", "Week 16 responder", "At least 20% improvement in joint counts at Week 16?", scope="decision", ask_at={"visit": "w16", "n": None}),
            question("enter_ole", "Enters OLE", "Does the patient enter the open-label extension?", scope="decision", ask_at={"visit": "w24", "n": None}),
        ],
        visits=[
            visit("scr", "SCR", "Screening", "Screening", "screening", 0, procedures=[
                "Informed consent", "Inclusion / exclusion criteria", "Medical history, demographics", "Physical examination", "Vital signs", "Body weight",
                "12-lead ECG", proc("Chest X-ray", {"question": "recent_cxr", "in": ["no"]}, reuse_within_days=90), "Tuberculosis test (IGRA)",
                "Hepatitis B, hepatitis C and HIV serology", proc("Serum pregnancy test", wocbp), "Haematology, chemistry", "hs-CRP",
                "Tender and swollen joint counts (68/66)", "HAQ-DI, patient global, pain VAS", "Adverse events, concomitant medication"]),
            visit("w0", "W0", "Baseline (Week 0)", "Part 1", "baseline", 0, procedures=[
                "Inclusion / exclusion criteria", "Physical examination", "Body weight", preg, "Fasting lipid panel", "hs-CRP", "PASI and body surface area",
                "Enthesitis and dactylitis assessment", "SF-36, FACIT-Fatigue", "Radiographs of hands and feet", "PK and anti-drug antibody sample",
                "Randomisation", *common], prep={"fasting_hours": 8, "notes": []}),
            *[visit(f"dose{n}", f"W{n}-INJ", f"Week {n} study drug injection", "Part 1", "baseline", 0, (0, 0), ["Study drug injection at site"],
                    follows=f"w{n}") for n in doses],
            p1(2, []),
            p1(4, [preg, "hs-CRP", "PASI and body surface area", "Enthesitis and dactylitis assessment", "PK and anti-drug antibody sample"]),
            p1(8, [preg, "hs-CRP"]),
            p1(12, ["Physical examination", "Body weight", "12-lead ECG", preg, "Fasting lipid panel", "hs-CRP", "PASI and body surface area",
                    "Enthesitis and dactylitis assessment", "SF-36, FACIT-Fatigue", "PK and anti-drug antibody sample"]),
            p1(16, [preg, "hs-CRP", "PASI and body surface area", "Enthesitis and dactylitis assessment", "Responder assessment for early escape",
                    proc("Radiographs of hands and feet", escape)]),
            visit("w18", "W18", "Week 18 early-escape safety visit", "Part 1", "baseline", 126, (3, 3), ["Vital signs", "Haematology, chemistry", "Adverse events"],
                  include_if=[escape]),
            p1(20, [preg, "hs-CRP"]),
            visit("w24", "W24", "Week 24", "Part 1", "baseline", 168, (5, 5), w24, prep={"fasting_hours": 8, "notes": []}),
            ole(28, [proc("Study drug injection at site"), "Dispense study drug for home use"]), ole(36, ["Dispense study drug for home use"]),
            ole(48, ["Physical examination", "Tuberculosis test (IGRA)", "PK and anti-drug antibody sample", "Dispense study drug for home use"]),
            ole(60, ["Dispense study drug for home use"]),
            ole(72, ["Physical examination", "Radiographs of hands and feet", "PK and anti-drug antibody sample"]),
            visit("sfu_ole", "SFU", "Safety follow-up (Week 76)", "Follow-up", "baseline", 532, (7, 7),
                  ["Vital signs", "Physical examination", preg, "Haematology, chemistry", "Adverse events, concomitant medication"], include_if=[ole_yes]),
            visit("sfu", "SFU", "Safety follow-up (8 weeks after the last dose)", "Follow-up", "last_dose", 56, (7, 7),
                  ["Vital signs", "Physical examination", preg, "Haematology, chemistry", "Adverse events, concomitant medication"],
                  include_if=[{"any": [ole_no, stopped]}]),
        ],
        events=[
            event("stop_drug", "Permanent discontinuation of study drug", visits=[
                visit("et", "ET", "Early termination visit", "Part 1", "screening", 0, (0, 14), w24)]),
            event("withdraw", "Withdrawal of consent", ends_schedule=True),
            event("alt_3x", "ALT or AST above 3 x ULN", repeatable=True, visits=[
                visit("lft1", "LFT", "Repeat liver function tests", "Safety", "screening", 2, (0, 1), ["Liver function tests"]),
                visit("lftw", "LFT-W", "Weekly liver function tests", "Safety", "screening", 9, (0, 1), ["Liver function tests"],
                      repeat=repeat(7, None, chain=True, until_check=True, label_pattern="Weekly liver function tests {n}", code_pattern="LFT-W{n}"),
                      check={"id": "lft_ok", "text": "ALT and AST both below 2 x ULN?", "help": None, "recheck_days": None, "max_wait_days": None,
                             "on_timeout": "warn", "stop_visits": [], "source": "Section 7.4", "measures": [], "limits": [], "dose_changes": []})]),
        ],
        rules=[
            rule("stop_drug_rule", "Study drug discontinued", {"event": "stop_drug"},
                 stop_visits=[*[f"dose{n}" for n in doses], "w28", "w36", "w48", "w60", "w72", "sfu_ole"],
                 effect="No further injections; Part 1 visits continue; no OLE; safety follow-up 8 weeks after the last dose."),
            rule("withdraw_rule", "Consent withdrawn", {"event": "withdraw"}, stop_all=True, effect="No further visits."),
        ])
