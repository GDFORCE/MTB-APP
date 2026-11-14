"""SYN-04: two-period crossover PK study with a washout range, confinement and a schedule table
laid out with spacing only (no ruled lines).

Constructed fixture, not a real protocol. Style: phase 1 CRO template.

Rules it carries:
- Screening Day -21 to Day -2 before Period 1 dosing.
- Each period: check-in on Day -1 at least 10 hours before dosing; dosing on Day 1; PK samples
  pre-dose and 0.5, 1, 2, 4, 8, 12, 24 hours after dosing; discharge on Day 2 after the 24-hour
  sample; return on Day 3 for the 48-hour sample. There is no Day 0.
- Period 2 dosing at least 7 and at most 14 days after Period 1 dosing (entered per subject).
- Sequence AB or BA. Treatment B is given after a high-fat breakfast, so the breakfast is in
  Period 2 for sequence AB and in Period 1 for sequence BA.
- Safety laboratory tests at screening, at each check-in and on Day 3 of Period 2 only.
- Follow-up telephone call 7 days (+/-2) after the last dose.
- Withdrawal after Period 1: no Period 2; the follow-up call counts from the Period 1 dose.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, event, has, model, proc, question, rule, visit, win
from ..pdfgen import Pdf

ID = "syn04-crossover-borderless"
TITLE = "BE-117: two-period crossover with washout range (table without ruled lines)"
COVERS = ["table laid out with spacing only", "crossover periods", "washout range between periods", "date entered within a range",
          "check-in a number of hours before dosing", "confinement", "serial PK time points incl. next-day samples",
          "no Day 0 (Day -1 then Day 1)", "sequence-dependent assessment", "assessment in one period only",
          "visit after last dose", "withdrawal between periods"]

SCR, P1 = "2026-04-20", "2026-05-05"


def build_pdf(path: str) -> None:
    pdf = Pdf(header="Meridian Clinical Pharmacology Unit - Protocol BE-117 - Final 2.0 - 10 Mar 2026", footer="Sponsor: Halden Generics AG",
              font="cour", bold="cobo", size=9)
    pdf.title(["PROTOCOL BE-117", "An Open-Label, Randomised, Single-Dose, Two-Period, Two-Sequence Crossover Study of the Effect "
               "of Food on the Pharmacokinetics of Halvir 200 mg Tablets in Healthy Adult Subjects",
               "Sponsor: Halden Generics AG", "Clinical site: Meridian Clinical Pharmacology Unit", "Final 2.0, 10 March 2026"])
    pdf.new_page()
    pdf.h1("3  INVESTIGATIONAL PLAN")
    pdf.h2("3.1  Overall design")
    pdf.p("Each subject receives a single 200 mg Halvir tablet in each of two treatment periods. Treatment A: tablet "
          "taken after an overnight fast of at least 10 hours. Treatment B: tablet taken 30 minutes after the start of "
          "a standard high-fat breakfast. Subjects are randomised on Day -1 of Period 1 to sequence AB (Treatment A in "
          "Period 1, Treatment B in Period 2) or sequence BA (Treatment B in Period 1, Treatment A in Period 2).")
    pdf.p("Screening takes place from Day -21 to Day -2 before dosing in Period 1. In each period the day of dosing is "
          "Day 1 and the day before dosing is Day -1; there is no Day 0.")
    pdf.h2("3.2  Washout")
    pdf.p("Dosing in Period 2 takes place at least 7 days and not more than 14 days after dosing in Period 1.")
    pdf.h2("3.3  Confinement")
    pdf.p("In each period subjects are admitted to the unit on Day -1, at least 10 hours before the planned time of "
          "dosing, and remain in the unit until the 24-hour pharmacokinetic sample has been taken on Day 2. Subjects "
          "return to the unit on Day 3 for the 48-hour sample.")
    pdf.h2("3.4  Schedule of events")
    cols = [150, 68, 58, 58, 58, 58, 72]
    rows = [
        ["", "Screening", ("Each treatment period (1 and 2)", 4), "<", "<", "<", "Follow-up"],
        ["Procedure", "D-21 to D-2", "Day -1", "Day 1", "Day 2", "Day 3", "call"],
        ["Informed consent", "X", "", "", "", "", ""],
        ["Medical history", "X", "", "", "", "", ""],
        ["Physical examination", "X", "X", "", "", "", ""],
        ["Vital signs", "X", "X", "X", "X", "X", ""],
        ["12-lead ECG", "X", "", "X", "", "", ""],
        ["Safety laboratory tests", "X", "X", "", "", "X*", ""],
        ["Urine drug screen, alcohol test", "X", "X", "", "", "", ""],
        ["Pregnancy test (females)", "X", "X", "", "", "", ""],
        ["Randomisation (Period 1 only)", "", "X", "", "", "", ""],
        ["Admission to unit", "", "X", "", "", "", ""],
        ["Study drug administration", "", "", "X", "", "", ""],
        ["PK blood sampling", "", "", "X", "X", "X", ""],
        ["Discharge from unit", "", "", "", "X", "", ""],
        ["Adverse events", "X", "X", "X", "X", "X", "X"],
    ]
    pdf.table(rows, cols, ruled=False, size=8, header_rows=2)
    pdf.p("* Safety laboratory tests on Day 3 are performed in Period 2 only (end-of-study tests).", size=8)
    pdf.p("PK blood sampling times in each period: pre-dose (within 60 minutes before dosing) and 0.5, 1, 2, 4, 8 and 12 "
          "hours after dosing on Day 1, 24 hours after dosing on Day 2 (before discharge) and 48 hours after dosing on "
          "Day 3. Samples up to 2 hours are taken within 2 minutes of the nominal time, later samples within 10 minutes; "
          "the 48-hour sample may be taken within 2 hours of the nominal time.", size=8)
    pdf.h2("3.5  Follow-up")
    pdf.p("A follow-up telephone call is made 7 days (plus or minus 2 days) after the last dose of study drug.")
    pdf.h2("3.6  Withdrawal")
    pdf.p("A subject who withdraws or is withdrawn after dosing in Period 1 does not attend Period 2. The follow-up "
          "telephone call is made 7 days (plus or minus 2 days) after that subject's last dose.")
    pdf.save(path)


def dates(p2: str | None) -> list[dict[str, Any]]:
    out = [{"what": ["screening", "consent"], "date": SCR},
           {"what": [["period 1", "dos*"], ["period 1", "day 1"], ["p1d1"], ["p1", "dos*"], ["first dose"], ["period 1"]], "date": P1}]
    if p2:
        out.append({"what": [["period 2", "dos*"], ["period 2", "day 1"], ["p2d1"], ["p2", "dos*"], ["second dose"], ["period 2"]], "date": p2})
    return out


SEQ = lambda s: {"what": ["sequence"], "answer": [s]}  # noqa: E731
FEMALE = lambda yes: {"what": ["female", "sex", "childbearing", "pregnan*"], "answer": yes, "optional": True}  # noqa: E731


def w(period: int, what: str) -> list[Any]:
    p = [f"period {period}", f"p{period}"]
    names = {"in": ["check in", "admission", "day -1", "d -1"], "dose": ["day 1", "dosing", "dose", "d1"],
             "d2": ["day 2", "d2", "discharge"], "d3": ["day 3", "d3", "48"]}[what]
    return [[a, b] for a in p for b in names] + [f"p{period}{x}" for x in {"in": ["d-1", "ci"], "dose": ["d1"], "d2": ["d2"], "d3": ["d3"]}[what]]


LABS, PK, DRUGS = has("Safety laboratory tests", "safety lab*", "laboratory"), has("PK blood sampling", "pk", "pharmacokinetic*"), has("Urine drug screen", "drug screen")
MEAL, DOSE, ECG = has("High-fat breakfast", "breakfast", "high fat", "fed"), has("Study drug administration", "study drug", "halvir", "dosing", "administration"), has("12-lead ECG", "ecg")
FU = [["follow up"], ["followup"], ["fu"]]


def period(n: int, dose: str, fed: bool) -> list[dict[str, Any]]:
    return [
        ev(f"Period {n} check-in (Day -1)", w(n, "in"), d(dose, -1), has=[LABS, DRUGS, has("Vital signs", "vital*")], lacks=[PK, DOSE]),
        ev(f"Period {n} dosing (Day 1)", w(n, "dose"), dose, has=[DOSE, PK, ECG, *([MEAL] if fed else [])], lacks=[LABS, DRUGS, *([] if fed else [MEAL])],
           says=[{"name": "PK at 0.5, 1, 2, 4, 8, 12 h after dosing", "any": [["0.5 h", "8 h", "12 h"], ["30 min", "8 h", "12 h"], ["30", "480", "720"]]}]),
        ev(f"Period {n} Day 2 (24-hour sample, discharge)", w(n, "d2"), d(dose, 1), lacks=[LABS, DOSE]),
        ev(f"Period {n} Day 3 (48-hour sample)", w(n, "d3"), d(dose, 2), has=[*([LABS] if n == 2 else [])], lacks=[DOSE, *([] if n == 2 else [LABS])]),
    ]


def expected() -> dict[str, Any]:
    return {"fixture": ID, "constructed": True, "cases": [
        {"name": "A: sequence AB, Period 2 dosed 7 days after Period 1",
         "given": {"today": SCR, "dates": dates(d(P1, 7)), "facts": [SEQ("AB"), FEMALE(False)]},
         "expect": {"visits": [ev("Screening", ["screening", "scr"], SCR, has=[LABS, DRUGS, ECG], lacks=[PK, DOSE]),
                               *period(1, P1, False), *period(2, d(P1, 7), True),
                               ev("Follow-up call (7 days after the last dose)", FU, d(P1, 14), win(d(P1, 14), 2, 2), modality="phone")]}},
        {"name": "B: sequence BA, Period 2 dosed 10 days after Period 1",
         "given": {"today": SCR, "dates": dates(d(P1, 10)), "facts": [SEQ("BA"), FEMALE(True)]},
         "expect": {"visits": [*period(1, P1, True), *period(2, d(P1, 10), False),
                               ev("Follow-up call", FU, d(P1, 17), win(d(P1, 17), 2, 2), modality="phone")]}},
        {"name": "C: Period 2 date entered only 5 days after Period 1: outside the 7 to 14 day washout",
         "given": {"today": SCR, "dates": dates(d(P1, 5)), "facts": [SEQ("AB"), FEMALE(False)]},
         "expect": {"warnings": [{"name": "washout shorter than 7 days", "any": [["7"], ["washout"]]}]}},
        {"name": "D: Period 2 date not known yet: Period 2 waits for it (the protocol gives a range, not a date)",
         "given": {"today": SCR, "dates": dates(None), "facts": [SEQ("AB"), FEMALE(False)]},
         "expect": {"visits": [ev("Period 1 dosing", w(1, "dose"), P1), ev("Period 2 dosing (waiting for its date)", w(2, "dose"), None)]}},
        {"name": "E: withdrawn 3 days after Period 1 dosing: no Period 2, follow-up call 7 days after the Period 1 dose",
         "given": {"today": d(P1, 3), "dates": dates(None), "facts": [SEQ("AB"), FEMALE(False)],
                   "actuals": [{"visit": w(1, "dose"), "date": P1}],
                   "events": [{"what": [["withdraw*"], ["discontinu*"], ["early termination"]], "date": d(P1, 3)}]},
         "expect": {"visits": [ev("Follow-up call (7 days after the Period 1 dose)", FU, d(P1, 7), win(d(P1, 7), 2, 2), modality="phone")],
                    "absent": [{"name": "Period 2 check-in", "match": w(2, "in")}, {"name": "Period 2 dosing", "match": w(2, "dose")},
                               {"name": "Period 2 Day 3", "match": w(2, "d3")}]}},
    ]}


def hand_model() -> dict[str, Any]:
    female = {"question": "female", "in": ["yes"]}
    tps = [{"label": lb, "offset_min": m, "window_before_min": wb, "window_after_min": wa, "procedures": ["PK blood sample"]}
           for lb, m, wb, wa in (("Pre-dose", 0, 60, 0), ("0.5 h", 30, 2, 2), ("1 h", 60, 2, 2), ("2 h", 120, 2, 2), ("4 h", 240, 10, 10),
                                 ("8 h", 480, 10, 10), ("12 h", 720, 10, 10), ("24 h", 1440, 10, 10), ("48 h", 2880, 120, 120))]

    def per(n: int, anchor_id: str, fed_seq: str) -> list[dict[str, Any]]:
        p = f"p{n}"
        return [
            visit(f"{p}_in", f"P{n}D-1", f"Period {n} check-in (Day -1)", f"Period {n}", anchor_id, -1, procedures=[
                "Physical examination", "Vital signs", "Safety laboratory tests", "Urine drug screen, alcohol test", proc("Pregnancy test", female),
                *(["Randomisation"] if n == 1 else []), "Admission to unit", "Adverse events"],
                arrive_before={"visit": f"{p}_dose", "hours": 10}, duration_days=3),
            visit(f"{p}_dose", f"P{n}D1", f"Period {n} dosing (Day 1)", f"Period {n}", anchor_id, 0, procedures=[
                "Vital signs", "12-lead ECG", proc("High-fat breakfast 30 minutes before dosing", {"question": "sequence", "in": [fed_seq]}),
                "Study drug administration", "PK blood sampling", "Adverse events"], timepoints=tps),
            visit(f"{p}_d2", f"P{n}D2", f"Period {n} Day 2: 24-hour sample and discharge", f"Period {n}", anchor_id, 1, procedures=[
                "Vital signs", "PK blood sampling (24 h)", "Discharge from unit", "Adverse events"]),
            visit(f"{p}_d3", f"P{n}D3", f"Period {n} Day 3: 48-hour sample", f"Period {n}", anchor_id, 2, procedures=[
                "Vital signs", "PK blood sampling (48 h)", *(["Safety laboratory tests"] if n == 2 else []), "Adverse events"]),
        ]

    return model(TITLE, "BE-117",
        anchors=[
            anchor("screening", "start", "Screening"),
            anchor("p1d1", "ask", "Period 1 dosing (Day 1)", is_day1=True, set_by_visit="p1_dose", question="Date of Period 1 dosing?",
                   range={"relative_to": "screening", "min_days": 2, "max_days": 21}, estimates=[{"when": [], "relative_to": "screening", "offset_days": 14}]),
            anchor("p2d1", "ask", "Period 2 dosing (Day 1)", set_by_visit="p2_dose", question="Date of Period 2 dosing?",
                   range={"relative_to": "p1d1", "min_days": 7, "max_days": 14}, estimates=[{"when": [], "relative_to": "p1d1", "offset_days": 7}]),
            anchor("last_dose", "last_visit", "Last dose of study drug", last_of=["p1_dose", "p2_dose"]),
        ],
        questions=[question("sequence", "Sequence", "Which treatment sequence?", kind="choice", options=[("AB", "AB"), ("BA", "BA")]),
                   question("female", "Female", "Is the subject female?")],
        visits=[
            visit("scr", "SCR", "Screening", "Screening", "screening", 0, procedures=[
                "Informed consent", "Medical history", "Physical examination", "Vital signs", "12-lead ECG", "Safety laboratory tests",
                "Urine drug screen, alcohol test", proc("Pregnancy test", female), "Adverse events"]),
            *per(1, "p1d1", "BA"), *per(2, "p2d1", "AB"),
            visit("fu", "FU", "Follow-up call", "Follow-up", "last_dose", 7, (2, 2), ["Adverse events"], modality="phone"),
        ],
        events=[event("withdraw", "Withdrawal from the study", ends_schedule=True, keeps_visits=["fu"])],
        rules=[rule("withdraw_rule", "Subject withdrawn", {"event": "withdraw"}, stop_all=True, keeps_visits=["fu"],
                    effect="No further period visits; follow-up call 7 days after the last dose.")])
