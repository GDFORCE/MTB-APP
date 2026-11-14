"""SYN-01: parallel-group study, ruled schedule table with merged header cells and letter footnotes.

Constructed fixture, not a real protocol. Style: large-pharma template, numbered sections,
landscape schedule of activities.

Rules it carries (the expected answer below is derived from these by hand, not from the app):
- Screening Day -28 to Day -1; Day 1 = randomisation and first dose; no Day 0.
- Week 2 (Day 15 +/-2), Week 4 (Day 29 +/-3), Week 8 (Day 57 +/-3), Week 12 (Day 85 +/-3), all
  counted from Day 1 and never re-scheduled after a late visit.
- Week 2 is a telephone contact, except for participants aged 65 or older: a site visit with
  orthostatic vital signs and serum creatinine.
- Pregnancy test only for women of childbearing potential (serum at screening, urine later).
- PK sampling only in the PK sub-study: Day 1 pre-dose and 1, 2, 4 h; Week 4 pre-dose.
- Safety follow-up telephone contact 30 days after the last dose, window +7 days only.
- Early discontinuation of study drug: Early Termination visit within 7 days after the last dose
  with the Week 12 assessments; remaining treatment visits are not done; the follow-up contact stays.
- Withdrawal of consent: no further visits or contacts.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, event, has, model, proc, question, rule, visit, win
from ..pdfgen import Pdf

ID = "syn01-parallel-table"
TITLE = "NVX-204-201: 12-week parallel-group study (ruled table, merged header, footnotes)"
COVERS = ["ruled table", "merged header cells", "letter footnotes", "Day 1 convention", "screening range",
          "visit windows", "asymmetric window", "age-conditional visit mode", "sub-study assessments",
          "sex-conditional assessment", "intra-day PK time points", "visit after last dose",
          "early termination visit", "withdrawal of consent", "late visit does not move later visits"]

SCR, D1 = "2026-03-02", "2026-03-16"


def build_pdf(path: str) -> None:
    pdf = Pdf(header="Northvale Therapeutics  |  Protocol NVX-204-201  |  Version 3.0, 12 January 2026",
              footer="Confidential", font="helv", bold="hebo")
    pdf.title(["Clinical Study Protocol NVX-204-201",
               "A Phase 2, Randomised, Double-Blind, Placebo-Controlled, Parallel-Group Study of NVX-204 "
               "in Adults With Type 2 Diabetes Mellitus Inadequately Controlled on Metformin",
               "Sponsor: Northvale Therapeutics Ltd.", "Protocol version 3.0, 12 January 2026", "Phase 2"])
    pdf.new_page()
    pdf.h1("1. PROTOCOL SUMMARY")
    pdf.h2("1.1 Synopsis")
    pdf.p("Approximately 180 participants will be randomised 1:1 to NVX-204 50 mg or matching placebo, taken orally "
          "once daily for 12 weeks. The study consists of a screening period of up to 28 days (Day -28 to Day -1), a "
          "12-week double-blind treatment period and a safety follow-up telephone contact 30 days after the last dose "
          "of study drug. A pharmacokinetic (PK) sub-study will be conducted at selected sites in participants who give "
          "separate consent.")
    pdf.p("Day 1 is the day of randomisation and of the first dose of study drug. There is no Day 0: the day before "
          "Day 1 is Day -1.")
    pdf.h2("1.2 Schedule of Activities")
    pdf.p("The Schedule of Activities is given in Table 1. Further detail on each visit is given in Section 6.")
    pdf.new_page(landscape=True)
    pdf.text("Table 1  Schedule of Activities", font="hebo", size=10)
    X = "X"
    rows = [
        ["Period", "Screening", ("Double-Blind Treatment Period", 5), "<", "<", "<", "<", "Follow-up"],
        ["Visit", "V1", "V2", "V3", "V4", "V5", "V6 / ET(e)", "V7"],
        ["Week", "-4 to -1", "0", "2", "4", "8", "12", "30 days after last dose"],
        ["Study day", "-28 to -1", "1", "15", "29", "57", "85", ""],
        ["Visit window (days)", "", "", "+/-2", "+/-3", "+/-3", "+/-3", "+7"],
        ["Informed consent", X, "", "", "", "", "", ""],
        ["Eligibility criteria", X, X, "", "", "", "", ""],
        ["Demographics and medical history", X, "", "", "", "", "", ""],
        ["Physical examination(a)", X, "", "", "", "", X, ""],
        ["Vital signs", X, X, "X(b)", X, X, X, ""],
        ["12-lead ECG", X, X, "", "", "", X, ""],
        ["Haematology and clinical chemistry", X, X, "X(b)", X, "", X, ""],
        ["HbA1c", X, "", "", "", "", X, ""],
        ["Pregnancy test(c)", X, X, "", X, X, X, ""],
        ["Randomisation", "", X, "", "", "", "", ""],
        ["Dispense study drug", "", X, "", X, X, "", ""],
        ["Study drug accountability", "", "", "", X, X, X, ""],
        ["PK blood sampling(d)", "", X, "", X, "", "", ""],
        ["Adverse events", X, X, X, X, X, X, X],
        ["Concomitant medications", X, X, X, X, X, X, X],
    ]
    pdf.table(rows, [190, 70, 62, 62, 62, 62, 78, 110], header_rows=5)
    pdf.text("ET = early termination; ECG = electrocardiogram; HbA1c = glycated haemoglobin; PK = pharmacokinetic.", size=7.5, gap=2)
    for note in (
        "(a) Full physical examination at Screening; brief symptom-directed examination at Week 12 / ET.",
        "(b) Visit 3 is a telephone contact (adverse events and concomitant medications only). For participants aged 65 "
        "years or older at Screening, Visit 3 is instead performed at the study site and additionally includes "
        "orthostatic vital signs and serum creatinine.",
        "(c) Women of childbearing potential only. Serum test at Screening; urine test at all other marked visits.",
        "(d) PK sub-study participants only. Day 1: pre-dose and 1, 2 and 4 hours post-dose (each +/-10 minutes). "
        "Week 4: one pre-dose sample (within 30 minutes before dosing).",
        "(e) Participants who permanently discontinue study drug before Week 12 attend an Early Termination (ET) visit "
        "within 7 days after the last dose of study drug; the Week 12 assessments are performed at the ET visit.",
    ):
        pdf.text(note, size=7.5, gap=1)
    pdf.new_page()
    pdf.h1("6. STUDY VISITS AND PROCEDURES")
    pdf.h2("6.1 General")
    pdf.p("All visits after Day 1 are scheduled relative to Day 1 (the date of the first dose). Visit windows are given "
          "in Table 1. If a visit is performed outside its window, the deviation is recorded and subsequent visits are "
          "NOT re-scheduled: they remain on the original schedule calculated from Day 1. No visit window applies to "
          "Screening or to Day 1.")
    pdf.h2("6.2 Study Drug Administration")
    pdf.p("Study drug is taken once daily in the morning. On the days of Visit 2 and Visit 4 the dose is taken at the "
          "study site after the pre-dose assessments. The final dose of study drug is taken on the morning of the "
          "Week 12 visit (Visit 6).")
    pdf.h2("6.3 Safety Follow-up (Visit 7)")
    pdf.p("A safety follow-up telephone contact is made 30 days after the last dose of study drug. The contact may be "
          "made up to 7 days later but not earlier (window +7 days). It applies to all randomised participants, "
          "including those who discontinue study drug early, unless consent has been withdrawn.")
    pdf.h1("7. DISCONTINUATION AND WITHDRAWAL")
    pdf.h2("7.1 Permanent Discontinuation of Study Drug")
    pdf.p("A participant who permanently discontinues study drug before Week 12 should attend an Early Termination (ET) "
          "visit within 7 days after the last dose of study drug. The assessments listed for Week 12 are performed at "
          "the ET visit. No further treatment-period visits are performed after study drug has been discontinued. The "
          "safety follow-up telephone contact (Visit 7) is still made 30 days after the last dose.")
    pdf.h2("7.2 Withdrawal of Consent")
    pdf.p("A participant may withdraw consent at any time. No further study visits, contacts or procedures are "
          "performed after withdrawal of consent, including the ET visit and the safety follow-up contact.")
    pdf.save(path)


# ── expected answer (hand-derived from the rules above) ──────────────────
DATES = [{"what": ["screening", "consent", "visit 1"], "date": SCR},
         {"what": ["day 1", "randomi*", "first dose", "baseline", "visit 2"], "date": D1}]
WOCBP = lambda yes: {"what": ["childbearing", "wocbp", "sex", "female"], "answer": yes}  # noqa: E731
AGE = lambda n: [{"what": ["age", "65"], "answer": n, "optional": True},  # noqa: E731
                 {"what": ["age", "65"], "answer": n >= 65, "optional": True},
                 {"what": ["age", "65"], "answer": ["65 or older", ">=65", "65+", "older"] if n >= 65 else ["under 65", "<65", "younger", "below 65"], "optional": True}]
PK = lambda yes: {"what": ["pk", "pharmacokinetic*", "sub study", "substudy"], "answer": yes}  # noqa: E731

W = {"scr": ["screening", "scr", "v1", "visit 1"], "d1": ["day 1", "d1", "v2", "visit 2", "baseline", "randomi*", "week 0"],
     "w2": ["week 2", "w2", "v3", "visit 3", "day 15"], "w4": ["week 4", "w4", "v4", "visit 4", "day 29"],
     "w8": ["week 8", "w8", "v5", "visit 5", "day 57"], "w12": ["week 12", "w12", "v6", "visit 6", "day 85"],
     "fu": ["follow up", "followup", "fu", "v7", "visit 7", "safety contact", "safety call"],
     "et": ["early termination", "et", "early discontinuation"]}
STOP = [["discontinu*", "study drug"], ["discontinu*", "treatment"], ["early termination"], ["stop*", "study drug"],
        ["treatment", "discontinu*"], ["study drug", "discontinu*"]]
WITHDRAW = [["withdraw*", "consent"], ["consent", "withdraw*"]]

VITALS, ECG, LABS = has("Vital signs", "vital*"), has("12-lead ECG", "ecg", "electrocardiogra*"), has("Haematology and chemistry", "haematolog*", "hematolog*", "chemistry")
PREG, PKS = has("Pregnancy test", "pregnancy"), has("PK sampling", "pk", "pharmacokinetic*")
HBA1C, AE = has("HbA1c", "hba1c", "glycated"), has("Adverse events", "adverse event*", "ae", "aes")
DISPENSE, PE = has("Dispense study drug", "dispens*"), has("Physical examination", "physical exam*")
ORTHO, CREAT = has("Orthostatic vital signs", "orthostatic"), has("Serum creatinine", "creatinine")


def nominal(wocbp: bool, older: bool, pk: bool) -> list[dict[str, Any]]:
    cond = lambda items: [x for x, on in items if on]  # noqa: E731
    return [
        ev("Screening", W["scr"], SCR, has=[VITALS, ECG, LABS, HBA1C, PE, AE, *cond([(has("Serum pregnancy test", ["serum", "pregnancy"]), wocbp)])],
           lacks=[PKS, DISPENSE, *cond([(PREG, not wocbp)])]),
        ev("Day 1", W["d1"], D1, None, has=[VITALS, ECG, LABS, DISPENSE, has("Randomisation", "randomi*"), AE, *cond([(PREG, wocbp), (PKS, pk)])],
           lacks=[HBA1C, PE, *cond([(PREG, not wocbp), (PKS, not pk)])],
           **({"says": [{"name": "PK at pre-dose and 1, 2, 4 h post-dose", "any": [["1 h", "2 h", "4 h"], ["60", "120", "240"]]}]} if pk else {})),
        ev("Week 2", W["w2"], d(D1, 14), win(d(D1, 14), 2, 2), modality="site" if older else "phone", has=[AE, *cond([(ORTHO, older), (CREAT, older)])],
           lacks=[ECG, HBA1C, PREG, PKS, *cond([(ORTHO, not older), (CREAT, not older), (VITALS, not older)])]),
        ev("Week 4", W["w4"], d(D1, 28), win(d(D1, 28), 3, 3), modality="site", has=[VITALS, LABS, DISPENSE, AE, *cond([(PREG, wocbp), (PKS, pk)])],
           lacks=[ECG, HBA1C, PE, *cond([(PREG, not wocbp), (PKS, not pk)])]),
        ev("Week 8", W["w8"], d(D1, 56), win(d(D1, 56), 3, 3), has=[VITALS, DISPENSE, AE, *cond([(PREG, wocbp)])], lacks=[ECG, LABS, HBA1C, PKS, PE]),
        ev("Week 12", W["w12"], d(D1, 84), win(d(D1, 84), 3, 3), has=[VITALS, ECG, LABS, HBA1C, PE, AE, *cond([(PREG, wocbp)])], lacks=[PKS, DISPENSE]),
        ev("Safety follow-up contact (30 days after the last dose, +7)", W["fu"], d(D1, 84 + 30), [d(D1, 114), d(D1, 121)], modality="phone",
           has=[AE], lacks=[VITALS, LABS, PREG]),
    ]


def expected() -> dict[str, Any]:
    recorded = [{"visit": W["d1"], "date": D1, "all": True}, {"visit": W["w2"], "date": d(D1, 14), "all": True},
                {"visit": W["w4"], "date": d(D1, 28), "all": True}]
    return {"fixture": ID, "constructed": True, "cases": [
        {"name": "A: man under 65, not in the PK sub-study, on time",
         "given": {"today": SCR, "dates": DATES, "facts": [WOCBP(False), *AGE(54), PK(False)]},
         "expect": {"visits": nominal(False, False, False), "no_other_visits": True, "allow_extra": ["dose", "dosing"],
                    "flags": []}},
        {"name": "B: woman of childbearing potential aged 70, in the PK sub-study",
         "given": {"today": SCR, "dates": DATES, "facts": [WOCBP(True), *AGE(70), PK(True)]},
         "expect": {"visits": nominal(True, True, True), "no_other_visits": True, "allow_extra": ["dose", "dosing"]}},
        {"name": "C: Week 4 done late (Day 36, outside its window); later visits stay on the Day 1 grid",
         "given": {"today": d(D1, 36), "dates": DATES, "facts": [WOCBP(False), *AGE(54), PK(False)],
                   "actuals": [{"visit": W["d1"], "date": D1, "all": True}, {"visit": W["w4"], "date": d(D1, 35), "all": True}]},
         "expect": {"visits": [ev("Week 8", W["w8"], d(D1, 56), win(d(D1, 56), 3, 3)), ev("Week 12", W["w12"], d(D1, 84), win(d(D1, 84), 3, 3)),
                               ev("Safety follow-up contact", W["fu"], d(D1, 114), [d(D1, 114), d(D1, 121)])]}},
        {"name": "D: study drug stopped for good, last dose Day 41 (25 Apr): ET visit within 7 days, follow-up 30 days after",
         "given": {"today": d(D1, 40), "dates": DATES, "facts": [WOCBP(True), *AGE(54), PK(False)], "actuals": recorded,
                   "events": [{"what": STOP, "date": d(D1, 40)}]},
         "expect": {"visits": [
             ev("Early Termination visit (within 7 days after the last dose, Week 12 assessments)", W["et"], date_in=[d(D1, 40), d(D1, 47)],
                window=[d(D1, 40), d(D1, 47)], has=[VITALS, ECG, LABS, HBA1C, PE, PREG, AE], lacks=[PKS, DISPENSE]),
             ev("Safety follow-up contact (30 days after the last dose)", W["fu"], d(D1, 70), [d(D1, 70), d(D1, 77)], modality="phone")],
             "absent": [{"name": "Week 8 (no treatment visits after discontinuation)", "match": W["w8"], "after": d(D1, 40)},
                        {"name": "Week 12 (replaced by the ET visit)", "match": W["w12"], "after": d(D1, 40)}]}},
        {"name": "E: consent withdrawn on Day 36: nothing afterwards, no ET visit, no follow-up contact",
         "given": {"today": d(D1, 35), "dates": DATES, "facts": [WOCBP(False), *AGE(54), PK(False)], "actuals": recorded,
                   "events": [{"what": WITHDRAW, "date": d(D1, 35)}]},
         "expect": {"visits": [ev("Week 4 (already done)", W["w4"], d(D1, 28))],
                    "absent": [{"name": "Week 8", "match": W["w8"], "after": d(D1, 35)}, {"name": "Week 12", "match": W["w12"], "after": d(D1, 35)},
                               {"name": "Safety follow-up contact", "match": W["fu"], "after": d(D1, 35)},
                               {"name": "Early Termination visit", "match": W["et"], "after": D1}]}},
    ]}


# ── hand-written model (engine level: no AI) ─────────────────────────────
def hand_model() -> dict[str, Any]:
    wocbp = {"question": "wocbp", "in": ["yes"]}
    pk = {"question": "pk", "in": ["yes"]}
    old, young = {"question": "age", "op": ">=", "value": 65}, {"question": "age", "op": "<", "value": 65}
    w12 = ["Brief physical examination", "Vital signs", "12-lead ECG", "Haematology and clinical chemistry", "HbA1c",
           proc("Urine pregnancy test", wocbp), "Study drug accountability", "Adverse events", "Concomitant medications"]
    return model(TITLE, "NVX-204-201",
        anchors=[
            anchor("screening", "start", "Screening"),
            anchor("day1", "ask", "Day 1 (randomisation, first dose)", is_day1=True, set_by_visit="v2", question="Date of Day 1?",
                   range={"relative_to": "screening", "min_days": 1, "max_days": 28},
                   estimates=[{"when": [], "relative_to": "screening", "offset_days": 14}]),
            anchor("last_dose", "last_visit", "Last dose of study drug", last_of=["v6"], stop_is_last_dose=True),
        ],
        questions=[question("wocbp", "Childbearing potential", "Woman of childbearing potential?"),
                   question("age", "Age", "Age at screening (years)?", kind="number"),
                   question("pk", "PK sub-study", "In the PK sub-study?")],
        visits=[
            visit("v1", "V1", "Screening", "Screening", "screening", 0, procedures=[
                "Informed consent", "Eligibility criteria", "Demographics and medical history", "Full physical examination", "Vital signs",
                "12-lead ECG", "Haematology and clinical chemistry", "HbA1c", proc("Serum pregnancy test", wocbp), "Adverse events",
                "Concomitant medications"]),
            visit("v2", "V2", "Day 1", "Treatment", "day1", 0, procedures=[
                "Eligibility criteria", "Vital signs", "12-lead ECG", "Haematology and clinical chemistry", proc("Urine pregnancy test", wocbp),
                "Randomisation", "Dispense study drug", "Adverse events", "Concomitant medications"],
                timepoints=[{"label": lb, "offset_min": m, "window_before_min": wb, "window_after_min": wa, "procedures": [proc("PK blood sample", pk)]}
                            for lb, m, wb, wa in (("Pre-dose", 0, 60, 0), ("1 h post-dose", 60, 10, 10), ("2 h post-dose", 120, 10, 10), ("4 h post-dose", 240, 10, 10))]),
            visit("v3p", "V3", "Week 2 telephone contact", "Treatment", "day1", 14, (2, 2), ["Adverse events", "Concomitant medications"],
                  modality="phone", include_if=[young]),
            visit("v3s", "V3", "Week 2 site visit", "Treatment", "day1", 14, (2, 2),
                  ["Vital signs", "Orthostatic vital signs", "Serum creatinine", "Adverse events", "Concomitant medications"], include_if=[old]),
            visit("v4", "V4", "Week 4", "Treatment", "day1", 28, (3, 3), [
                "Vital signs", "Haematology and clinical chemistry", proc("Urine pregnancy test", wocbp), "Dispense study drug",
                "Study drug accountability", proc("PK blood sample (pre-dose)", pk), "Adverse events", "Concomitant medications"]),
            visit("v5", "V5", "Week 8", "Treatment", "day1", 56, (3, 3), [
                "Vital signs", proc("Urine pregnancy test", wocbp), "Dispense study drug", "Study drug accountability", "Adverse events",
                "Concomitant medications"]),
            visit("v6", "V6", "Week 12 / End of treatment", "Treatment", "day1", 84, (3, 3), w12),
            visit("v7", "V7", "Safety follow-up contact", "Follow-up", "last_dose", 30, (0, 7), ["Adverse events", "Concomitant medications"],
                  modality="phone"),
        ],
        events=[
            event("stop_drug", "Permanent discontinuation of study drug", ends_schedule=True, keeps_visits=["v7"], visits=[
                visit("et", "ET", "Early Termination visit", "Treatment", "screening", 0, (0, 7), w12)]),
            event("withdraw", "Withdrawal of consent", ends_schedule=True),
        ],
        rules=[
            rule("stop_drug_rule", "Study drug discontinued", {"event": "stop_drug"}, stop_all=True, keeps_visits=["v7"],
                 effect="No further treatment visits; ET visit within 7 days; follow-up 30 days after the last dose."),
            rule("withdraw_rule", "Consent withdrawn", {"event": "withdraw"}, stop_all=True, effect="No further visits or contacts."),
        ])
