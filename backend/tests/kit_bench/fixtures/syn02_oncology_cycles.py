"""SYN-02: oncology treatment cycles with lab-gated dosing, dose levels, calendar imaging and
survival follow-up.

Constructed fixture, not a real protocol. Style: cooperative-group protocol ("5.0 TREATMENT PLAN"),
serif type, study calendar with merged cycle headers, dosing tables in prose sections.

Rules it carries:
- Baseline within 21 days before Cycle 1 Day 1 (C1D1). Cycles of 21 days, up to 6 cycles.
  Zetarabine on Day 1 and Day 8 of every cycle; a Day 15 safety visit in Cycle 1 only.
- Day 1 needs ANC >= 1.5 and platelets >= 100 (x 10^9/L). Otherwise the cycle is delayed, CBC is
  repeated weekly, and a cycle starts on the day treatment is given (later dates count from it).
  No recovery within 21 days of the planned Day 1: protocol treatment is discontinued.
- Day 8: ANC >= 1.0 and platelets >= 75: full dose; ANC 0.5-0.99 or platelets 50-74: 75% of the
  dose; ANC < 0.5 or platelets < 50: Day 8 is omitted and not made up (the next cycle keeps its date).
- Dose levels 1000 / 800 / 600 mg/m2. One level lower for all later doses after febrile neutropenia,
  or after a cycle delay of more than 7 days. Below 600 mg/m2: discontinue.
- CT every 6 weeks (+/-7 days) counted from C1D1 on the calendar, whatever happens to the cycles,
  until progression.
- End of treatment (EOT) visit 30 days (+/-7) after the last dose, or as soon as possible.
- Survival follow-up by telephone every 12 weeks (+/-14 days) from the EOT visit.
- Progression: no further treatment; EOT and survival follow-up continue.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, event, has, model, proc, question, repeat, rule, visit, win
from ..pdfgen import Pdf

ID = "syn02-oncology-cycles"
TITLE = "CCG-0412: 21-day chemotherapy cycles with lab-gated dosing and dose levels"
COVERS = ["treatment cycles (chained)", "days within a cycle", "cycle-1-only visit", "merged cycle headers",
          "lab-gated dose (hold and re-check)", "discontinue after a maximum delay", "same-day dosing table (give / reduce / omit)",
          "dose levels and reductions", "event-driven dose reduction", "fixed calendar grid independent of delays",
          "repeat until progression", "visit after last dose", "repeat counted from another visit's actual date",
          "progression stops treatment"]

BASE, C1 = "2026-01-20", "2026-02-03"


def build_pdf(path: str) -> None:
    pdf = Pdf(header="CCG-0412  Version Date: 05-JAN-2026", footer="Central Cancer Group", font="tiro", bold="tibo", size=10.5)
    pdf.title(["CCG-0412", "A Phase II Study of Zetarabine in Patients With Previously Treated Advanced Biliary Tract Carcinoma",
               "CENTRAL CANCER GROUP", "Version Date: 05-JAN-2026", "Study Chair: (constructed fixture)"])
    pdf.new_page()
    pdf.h1("5.0 TREATMENT PLAN")
    pdf.h2("5.1 Registration and Baseline")
    pdf.p("Baseline evaluations must be completed within 21 days before Cycle 1 Day 1 (C1D1). Baseline tumour imaging "
          "(CT of chest, abdomen and pelvis) must be performed within those 21 days.")
    pdf.h2("5.2 Zetarabine Administration")
    pdf.p("Zetarabine 1000 mg/m2 is given as a 30-minute intravenous infusion on Day 1 and Day 8 of each 21-day cycle. "
          "Treatment continues for a maximum of 6 cycles, or until disease progression, unacceptable toxicity or "
          "patient withdrawal, whichever occurs first. In Cycle 1 only, patients also attend the clinic on Day 15 for "
          "a complete blood count (CBC) and toxicity assessment; no treatment is given on Day 15.")
    pdf.p("A cycle begins on the day zetarabine is actually administered on Day 1. Day 8, and Day 1 of the following "
          "cycle, are counted from that actual date. Day 1 may be given up to 2 days early or late for scheduling "
          "reasons; Day 8 may be given 1 day early or late.")
    pdf.h1("6.0 DOSE MODIFICATIONS")
    pdf.h2("6.1 Dose Levels")
    pdf.table([["Dose level", "Zetarabine dose"], ["0 (starting dose)", "1000 mg/m2"], ["-1", "800 mg/m2"], ["-2", "600 mg/m2"]],
              [150, 150], size=9, center_from=9)
    pdf.p("Doses that have been reduced are not re-escalated. Patients who require a reduction below dose level -2 "
          "discontinue protocol treatment.")
    pdf.h2("6.2 Day 1 of Each Cycle")
    pdf.p("On Day 1 of each cycle the absolute neutrophil count (ANC) must be >= 1.5 x 10^9/L and the platelet count "
          ">= 100 x 10^9/L. If these values are not met, the cycle is delayed and the CBC is repeated weekly until they "
          "are met. If treatment cannot be resumed within 21 days of the planned Day 1 date, protocol treatment is "
          "permanently discontinued.")
    pdf.p("If a cycle has been delayed by more than 7 days for haematologic toxicity, zetarabine is reduced by one dose "
          "level for that cycle and all subsequent cycles.")
    pdf.h2("6.3 Day 8")
    pdf.table([["ANC (x 10^9/L)", "", "Platelets (x 10^9/L)", "Zetarabine on Day 8"],
               [">= 1.0", "and", ">= 75", "100% of the current dose"],
               ["0.5 to 0.99", "or", "50 to 74", "75% of the current dose (this administration only)"],
               ["< 0.5", "or", "< 50", "Omit. An omitted Day 8 dose is not made up."]],
              [95, 40, 110, 230], size=9, center_from=9)
    pdf.p("An omitted Day 8 dose does not change the date of the next cycle, which is due 21 days after Day 1 of the "
          "current cycle.")
    pdf.h2("6.4 Febrile Neutropenia")
    pdf.p("After an episode of febrile neutropenia (ANC < 1.0 x 10^9/L with a single temperature > 38.3 C), zetarabine "
          "is reduced by one dose level for all subsequent doses.")
    pdf.h1("7.0 STUDY CALENDAR")
    pdf.new_page(landscape=True)
    X = "X"
    rows = [
        ["Required studies", "Baseline (a)", ("Cycle 1", 3), "<", "<", ("Cycles 2 to 6", 2), "<", "End of treatment (d)", "Follow-up (e)"],
        ["", "", "Day 1", "Day 8", "Day 15", "Day 1", "Day 8", "", ""],
        ["History and physical examination", X, X, "", "", X, "", X, ""],
        ["ECOG performance status", X, X, "", "", X, "", X, ""],
        ["CBC with differential (b)", X, X, X, X, X, X, X, ""],
        ["Serum chemistry", X, X, "", "", X, "", X, ""],
        ["Serum pregnancy test (women of childbearing potential)", X, "", "", "", "", "", "", ""],
        ["CT chest / abdomen / pelvis (c)", X, ("Every 6 weeks from C1D1 (c)", 5), "<", "<", "<", "<", "", ""],
        ["Zetarabine", "", X, X, "", X, X, "", ""],
        ["Adverse event assessment", "", X, X, X, X, X, X, ""],
        ["Survival status", "", "", "", "", "", "", "", X],
    ]
    pdf.text("7.1 Study Calendar", font="tibo", size=11)
    pdf.table(rows, [200, 70, 55, 55, 55, 55, 55, 95, 80], header_rows=2, size=8)
    for note in (
        "a  Within 21 days before C1D1.",
        "b  CBC results must be available before each zetarabine dose (see Sections 6.2 and 6.3).",
        "c  Tumour imaging is performed every 6 weeks (+/- 7 days) counted from C1D1, according to the calendar and "
        "independent of treatment delays or omitted doses, until disease progression.",
        "d  30 days (+/- 7 days) after the last dose of zetarabine, or as soon as possible if the decision to stop "
        "treatment is made later than that.",
        "e  By telephone every 12 weeks (+/- 14 days) from the end of treatment visit until death.",
    ):
        pdf.text(note, size=8.5, gap=1)
    pdf.new_page()
    pdf.h1("8.0 CRITERIA FOR REMOVAL FROM PROTOCOL TREATMENT")
    pdf.p("Protocol treatment is discontinued for: completion of 6 cycles; disease progression; a treatment delay of "
          "more than 21 days (Section 6.2); a required dose reduction below dose level -2; unacceptable toxicity; or "
          "patient withdrawal. After disease progression no further zetarabine is given and tumour imaging stops. All "
          "patients who discontinue protocol treatment for any reason have the end of treatment visit and are then "
          "followed for survival (Section 7.1, footnotes d and e).")
    pdf.save(path)


DATES = [{"what": ["baseline", "screening", "registration"], "date": BASE},
         {"what": ["c1d1", "cycle 1 day 1", "day 1", "first dose", "treatment start", "start of treatment"], "date": C1}]
FACTS = [{"what": ["childbearing", "wocbp", "sex", "female"], "answer": False, "optional": True}]


def cyc(n: int, day: int) -> list[Any]:
    return [f"cycle {n} day {day}", f"c{n}d{day}", f"c{n} d{day}", [f"cycle {n}", f"day {day}"]]


CBC, CHEM = has("CBC", "cbc", "blood count"), has("Serum chemistry", "chemistry")
ZETA, PE = has("Zetarabine", "zetarabine"), has("History and physical examination", "physical exam*", "history")
CT = [["ct"], ["imaging"], ["tumour assessment"], ["tumor assessment"], ["scan"]]
EOT = [["end of treatment"], ["eot"]]
SFU = [["survival"], ["follow up"], ["followup"]]
VAL = lambda anc, plt, answer: {"values": {"anc": anc, "platelet*": plt}, "answer": answer}  # noqa: E731


def dose_days(start: str = C1, cycles: range = range(1, 7), shift: dict[int, int] | None = None) -> dict[tuple[int, int], str]:
    """Day 1 / Day 8 dates when each cycle starts 21 days after the previous one's Day 1."""
    out: dict[tuple[int, int], str] = {}
    day1 = start
    for n in range(1, 7):
        day1 = d(day1, (shift or {}).get(n, 0))
        if n in cycles:
            out[(n, 1)], out[(n, 8)] = day1, d(day1, 7)
        day1 = d(day1, 21)
    return out


def expected() -> dict[str, Any]:
    on_time = dose_days()
    cases = []
    cases.append({
        "name": "A: six cycles on time",
        "given": {"today": BASE, "dates": DATES, "facts": FACTS},
        "expect": {"visits": [
            ev("Baseline", ["baseline", "screening"], BASE, has=[CBC, CHEM, PE, has("CT", *CT)], lacks=[ZETA]),
            # C1D1 is the date everything counts from, so no window is checked on it.
            *[ev(f"Cycle {n} Day {day}", cyc(n, day), on_time[(n, day)],
                 **({} if (n, day) == (1, 1) else {"window": win(on_time[(n, day)], 2, 2) if day == 1 else win(on_time[(n, day)], 1, 1)}),
                 has=[CBC, ZETA, *([CHEM, PE] if day == 1 else [])], lacks=[] if day == 1 else [CHEM, PE]) for n in range(1, 7) for day in (1, 8)],
            ev("Cycle 1 Day 15 (Cycle 1 only)", cyc(1, 15), d(C1, 14), has=[CBC], lacks=[ZETA, CHEM]),
            *[ev(f"CT week {6 * k}", CT, d(C1, 42 * k), win(d(C1, 42 * k), 7, 7)) for k in (1, 2, 3, 4)],
            ev("End of treatment (30 days after the last dose)", EOT, d(on_time[(6, 8)], 30), win(d(on_time[(6, 8)], 30), 7, 7), has=[CBC, CHEM, PE], lacks=[ZETA]),
            ev("Survival follow-up 1 (12 weeks after EOT)", SFU, d(on_time[(6, 8)], 30 + 84), win(d(on_time[(6, 8)], 114), 14, 14), modality="phone"),
        ], "absent": [{"name": "Cycle 7 (maximum 6 cycles)", "match": cyc(7, 1)}, {"name": "Cycle 2 Day 15 (Cycle 1 only)", "match": cyc(2, 15)}]},
    })
    late = dose_days(shift={3: 7})
    cases.append({
        "name": "B: Cycle 3 Day 1 held one week for ANC 1.1 (not more than 7 days: no dose reduction); CT stays on the calendar",
        "given": {"today": d(C1, 49), "dates": DATES, "facts": FACTS,
                  "checks": [{"visit": cyc(3, 1), "n": 3, "entries": [{"date": on_time[(3, 1)], **VAL(1.1, 150, "no")}, {"date": d(on_time[(3, 1)], 7), **VAL(1.8, 150, "yes")}]}]},
        "expect": {"visits": [
            ev("Cycle 3 Day 1 (given a week late)", cyc(3, 1), late[(3, 1)]), ev("Cycle 3 Day 8", cyc(3, 8), late[(3, 8)]),
            *[ev(f"Cycle {n} Day 1", cyc(n, 1), late[(n, 1)]) for n in (4, 5, 6)], ev("Cycle 6 Day 8", cyc(6, 8), late[(6, 8)]),
            ev("CT week 6 (calendar, not moved)", CT, d(C1, 42)), ev("CT week 12", CT, d(C1, 84)), ev("CT week 18", CT, d(C1, 126)),
            ev("End of treatment", EOT, d(late[(6, 8)], 30)),
        ], "absent": [{"name": "a dose reduction after a delay of exactly 7 days", "match": [["800"]]}]},
    })
    cases.append({
        "name": "C: Cycle 2 Day 8 omitted (ANC 0.4): not made up, Cycle 3 keeps its date",
        "given": {"today": d(C1, 28), "dates": DATES, "facts": FACTS,
                  "checks": [{"visit": cyc(2, 8), "n": 2, "entries": [{"date": on_time[(2, 8)], **VAL(0.4, 120, "skip")}]}]},
        "expect": {"visits": [ev("Cycle 2 Day 8 (omitted)", cyc(2, 8), on_time[(2, 8)], status="skipped"),
                              ev("Cycle 3 Day 1 (date unchanged)", cyc(3, 1), on_time[(3, 1)]), ev("Cycle 3 Day 8", cyc(3, 8), on_time[(3, 8)])]},
    })
    two = dose_days(shift={2: 14})
    cases.append({
        "name": "D: Cycle 2 delayed 14 days (more than 7): one dose level lower (800 mg/m2) from Cycle 2 on",
        "given": {"today": d(C1, 35), "dates": DATES, "facts": FACTS,
                  "checks": [{"visit": cyc(2, 1), "n": 2, "entries": [{"date": on_time[(2, 1)], **VAL(0.9, 160, "no")}, {"date": d(on_time[(2, 1)], 7), **VAL(1.2, 160, "no")},
                                                                      {"date": d(on_time[(2, 1)], 14), **VAL(1.6, 160, "yes")}]}]},
        "expect": {"visits": [
            ev("Cycle 1 Day 8 (still the starting dose)", cyc(1, 8), on_time[(1, 8)], says=[{"name": "1000 mg/m2", "any": [["1000"]]}]),
            ev("Cycle 2 Day 1 (two weeks late, reduced)", cyc(2, 1), two[(2, 1)], says=[{"name": "800 mg/m2 after a delay of more than 7 days", "any": [["800"]]}]),
            ev("Cycle 2 Day 8", cyc(2, 8), two[(2, 8)], says=[{"name": "800 mg/m2", "any": [["800"]]}]),
            ev("Cycle 3 Day 1", cyc(3, 1), two[(3, 1)], says=[{"name": "800 mg/m2 (no re-escalation)", "any": [["800"]]}]),
        ]},
    })
    cases.append({
        "name": "E: counts not recovered within 21 days of the planned Cycle 2 Day 1: treatment discontinued",
        "given": {"today": d(on_time[(2, 1)], 24), "dates": DATES, "facts": FACTS,
                  "actuals": [{"visit": cyc(1, 1), "date": on_time[(1, 1)]}, {"visit": cyc(1, 8), "date": on_time[(1, 8)]}],
                  "checks": [{"visit": cyc(2, 1), "n": 2, "entries": [{"date": d(on_time[(2, 1)], 7 * k), **VAL(0.8, 90, "no")} for k in range(4)]}]},
        "expect": {"visits": [
            ev("End of treatment (30 days after the last dose given, C1D8; as soon as possible once treatment has stopped)", EOT,
               date_in=[d(on_time[(1, 8)], 30), d(on_time[(2, 1)], 28)]),
            ev("CT week 6 (imaging continues until progression)", CT, d(C1, 42))],
            "absent": [{"name": "Cycle 2 Day 8", "match": cyc(2, 8)}, {"name": "Cycle 3 Day 1", "match": cyc(3, 1)}, {"name": "Cycle 6 Day 1", "match": cyc(6, 1)}]},
    })
    prog = d(on_time[(5, 1)], 2)
    cases.append({
        "name": "F: progression found two days after Cycle 5 Day 1: no more treatment or imaging; EOT and survival follow-up",
        "given": {"today": prog, "dates": DATES, "facts": FACTS,
                  "actuals": [{"visit": cyc(n, day), "date": on_time[(n, day)]} for n in range(1, 6) for day in (1, 8) if (n, day) != (5, 8)],
                  "events": [{"what": [["progress*"]], "date": prog}]},
        "expect": {"visits": [
            ev("End of treatment (30 days after Cycle 5 Day 1, the last dose)", EOT, d(on_time[(5, 1)], 30), win(d(on_time[(5, 1)], 30), 7, 7)),
            ev("Survival follow-up 1", SFU, d(on_time[(5, 1)], 114), modality="phone")],
            "absent": [{"name": "Cycle 5 Day 8", "match": cyc(5, 8), "after": prog}, {"name": "Cycle 6 Day 1", "match": cyc(6, 1)},
                       {"name": "CT after progression", "match": CT, "after": prog}]},
    })
    fn = d(on_time[(2, 8)], 4)
    cases.append({
        "name": "G: febrile neutropenia during Cycle 2: 800 mg/m2 for all later doses",
        "given": {"today": fn, "dates": DATES, "facts": FACTS, "events": [{"what": [["febrile neutropenia"]], "date": fn}]},
        "expect": {"visits": [
            ev("Cycle 2 Day 8 (before the episode: starting dose)", cyc(2, 8), on_time[(2, 8)], says=[{"name": "1000 mg/m2", "any": [["1000"]]}]),
            ev("Cycle 3 Day 1 (reduced)", cyc(3, 1), on_time[(3, 1)], says=[{"name": "800 mg/m2 after febrile neutropenia", "any": [["800"]]}]),
            ev("Cycle 6 Day 8 (still reduced)", cyc(6, 8), on_time[(6, 8)], says=[{"name": "800 mg/m2", "any": [["800"]]}]),
        ]},
    })
    return {"fixture": ID, "constructed": True, "cases": cases}


def hand_model() -> dict[str, Any]:
    wocbp = {"question": "wocbp", "in": ["yes"]}
    anc = lambda op, v: {"measure": "anc", "op": op, "value": v}  # noqa: E731
    plt = lambda op, v: {"measure": "plt", "op": op, "value": v}  # noqa: E731
    return model(TITLE, "CCG-0412",
        anchors=[
            anchor("baseline", "start", "Baseline"),
            anchor("c1d1", "ask", "Cycle 1 Day 1", is_day1=True, set_by_visit="d1", question="Date of Cycle 1 Day 1?",
                   range={"relative_to": "baseline", "min_days": 0, "max_days": 21},
                   estimates=[{"when": [], "relative_to": "baseline", "offset_days": 14}]),
            anchor("progression", "ask", "Disease progression", question="Date of progression?"),
            anchor("last_dose", "last_visit", "Last dose of zetarabine", last_of=["d1", "d8"]),
            anchor("eot_date", "relative", "End of treatment visit", relative_to="last_dose", offset_days=30, set_by_visit="eot"),
        ],
        questions=[question("wocbp", "Childbearing potential", "Woman of childbearing potential?")],
        measures=[{"id": "anc", "label": "ANC", "unit": "x 10^9/L", "kind": "value", "worse": "lower", "baseline": None},
                  {"id": "plt", "label": "Platelets", "unit": "x 10^9/L", "kind": "value", "worse": "lower", "baseline": None}],
        doses=[{"id": "zeta", "label": "Zetarabine", "levels": [{"id": "0", "label": "1000 mg/m2"}, {"id": "-1", "label": "800 mg/m2"},
                                                               {"id": "-2", "label": "600 mg/m2"}],
                "start": "0", "visits": ["d1", "d8"], "below_lowest": "stop", "stop_visits": ["d15"], "source": "Section 6.1"}],
        visits=[
            visit("base", "BL", "Baseline", "Baseline", "baseline", 0, procedures=[
                "History and physical examination", "ECOG performance status", "CBC with differential", "Serum chemistry",
                proc("Serum pregnancy test", wocbp), "CT chest / abdomen / pelvis"]),
            visit("d1", "C1D1", "Cycle 1 Day 1", "Treatment", "c1d1", 0, (2, 2), [
                "History and physical examination", "ECOG performance status", "CBC with differential", "Serum chemistry", "Zetarabine",
                "Adverse event assessment"],
                repeat=repeat(21, 6, chain=True, label_pattern="Cycle {n} Day 1", code_pattern="C{n}D1"),
                check={"id": "d1_counts", "text": "ANC >= 1.5 and platelets >= 100 (x 10^9/L)?", "help": None, "recheck_days": 7,
                       "max_wait_days": 21, "on_timeout": "stop", "stop_visits": ["d8", "d15"], "source": "Section 6.2",
                       "measures": ["anc", "plt"], "limits": [anc(">=", 1.5), plt(">=", 100)],
                       "dose_changes": [{"if": [{"held_days": {"op": ">", "value": 7}}], "dose": "zeta", "step": -1, "to": None, "stop": False,
                                         "occurrence": None, "and_later": False, "note": "cycle delayed more than 7 days"}]}),
            visit("d8", "C1D8", "Cycle 1 Day 8", "Treatment", "c1d1", 7, (1, 1), ["CBC with differential", "Zetarabine", "Adverse event assessment"],
                  follows="d1", repeat=repeat(21, 6, label_pattern="Cycle {n} Day 8", code_pattern="C{n}D8"),
                  check={"id": "d8_counts", "text": "ANC >= 1.0 and platelets >= 75 (x 10^9/L)?", "help": None, "recheck_days": None,
                         "max_wait_days": None, "on_timeout": "warn", "stop_visits": [], "source": "Section 6.3", "measures": ["anc", "plt"],
                         "limits": [], "dose_changes": [],
                         "actions": [{"if": [anc(">=", 1.0), plt(">=", 75)], "do": "give", "note": None},
                                     {"if": [anc(">=", 0.5), plt(">=", 50)], "do": "reduce", "percent": 75, "dose": "zeta", "note": None},
                                     {"if": [{"any": [anc("<", 0.5), plt("<", 50)]}], "do": "skip", "note": "Omitted, not made up"}]}),
            visit("d15", "C1D15", "Cycle 1 Day 15", "Treatment", "c1d1", 14, (0, 0), ["CBC with differential", "Adverse event assessment"], follows="d1"),
            visit("ct", "CT", "CT tumour assessment", "Treatment", "c1d1", 42, (7, 7), ["CT chest / abdomen / pelvis"],
                  repeat=repeat(42, None, open_ended=True, until_anchor="progression", label_pattern="CT week {n}", code_pattern="CT{n}")),
            visit("eot", "EOT", "End of treatment visit", "Follow-up", "last_dose", 30, (7, 7), [
                "History and physical examination", "ECOG performance status", "CBC with differential", "Serum chemistry",
                "Adverse event assessment"], asap_if_past=True),
            visit("sfu", "SFU", "Survival follow-up", "Follow-up", "eot_date", 84, (14, 14), ["Survival status"], modality="phone",
                  repeat=repeat(84, None, open_ended=True, label_pattern="Survival follow-up {n}", code_pattern="SFU{n}")),
        ],
        events=[
            event("progression", "Disease progression", sets_anchor="progression"),
            event("febrile_neutropenia", "Febrile neutropenia", repeatable=True),
            event("death", "Death", ends_schedule=True),
        ],
        rules=[
            rule("pd_stops_treatment", "Progression: treatment stops", {"event": "progression"}, stop_visits=["d1", "d8", "d15"],
                 effect="No further zetarabine; end of treatment visit and survival follow-up continue."),
            rule("fn_reduces", "Febrile neutropenia: one dose level lower", {"event": "febrile_neutropenia"},
                 dose_changes=[{"dose": "zeta", "step": -1, "to": None, "stop": False}], effect="Zetarabine one dose level lower for all later doses."),
            rule("death_ends", "Death", {"event": "death"}, stop_all=True, effect="No further visits."),
        ])
