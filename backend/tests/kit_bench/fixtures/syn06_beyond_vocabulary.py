"""SYN-06: a short protocol whose timing rules go beyond what the kit vocabulary could hold on
10 November 2026: calendar months, business days, a weekday rule, a consequence of consecutive
missed visits, and assessments carried over from a missed visit.

Constructed fixture, not a real protocol. Style: translated CRO protocol, numbered clauses.

The point of this fixture is not that the app computes all of this, but what it does with a rule
it cannot compute: it must tell the reviewer, with the protocol's words and page, and must not
turn the rule into a plausible wrong date without saying so.

Rules it carries:
- Day 1 = first infusion. Infusion visits on Day 1 and then once every calendar month on the same
  day of the month as Day 1 (the last day of the month when that day does not exist), Months 1-6,
  window +/-5 days.
- Home nursing visit 3 days after each infusion (+/-1) for participants living more than 50 km away.
- The site telephones the participant with laboratory results within 3 business days after each
  infusion visit.
- A missed infusion visit is not made up; its safety laboratory tests are added to the next visit.
- Two consecutive infusion visits missed: the participant is withdrawn (no further visits except
  the final visit).
- Final visit on the first Monday on or after Day 210.
- Fever of 38.5 C or more on two consecutive days: unscheduled visit within 24 hours.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, event, has, model, question, repeat, visit
from ..pdfgen import Pdf

ID = "syn06-beyond-vocabulary"
TITLE = "KMT-22: calendar months, business days, weekday and missed-visit rules"
COVERS = ["calendar-month intervals", "business days", "weekday rule for a visit", "consequence of consecutive missed visits",
          "assessment carried over from a missed visit", "distance-conditional home visit", "event-triggered visit within hours",
          "what the app does with rules it cannot compute"]

D1 = "2026-01-31"
# Same day of each calendar month as 31 January; the last day of the month where there is no 31st.
MONTHS = ["2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31"]
FINAL = "2026-08-31"  # Day 210 = 28 Aug 2026 (a Friday); the first Monday on or after it is 31 Aug.


def build_pdf(path: str) -> None:
    pdf = Pdf(header="Protocol No. KMT-22-03   Ver. 2.1   2026/01/09", footer="Kamiyama Bio Co., Ltd. / translated version",
              font="tiro", bold="tibo", size=10.5)
    pdf.title(["Protocol No. KMT-22-03", "A Multicenter, Open-Label, Single-Arm Study of KMT-22 Intravenous Infusion in Patients With "
               "Refractory Generalized Myasthenia Gravis", "Sponsor: Kamiyama Bio Co., Ltd.", "Version 2.1 (English translation), 9 January 2026"])
    pdf.new_page()
    pdf.h1("8. Study Schedule")
    for clause in (
        "8.1  The day of the first infusion of KMT-22 is defined as Day 1.",
        "8.2  Infusion visits. KMT-22 is infused at the study site on Day 1 and thereafter once every calendar month, on the same "
        "day of the month as Day 1, for 6 further infusions (Month 1 to Month 6). If that day does not exist in a month, the "
        "infusion visit is scheduled on the last day of that month. Each monthly infusion visit may be performed within 5 days "
        "before or after the scheduled date. At every infusion visit, vital signs, body weight, safety laboratory tests "
        "(hematology, biochemistry) and the MG-ADL score are assessed before the infusion.",
        "8.3  Home nursing visit. For participants whose residence is more than 50 km from the study site, a nurse visits the "
        "participant at home 3 days after each infusion (allowable range: 1 day before or after) to check vital signs and the "
        "infusion site. This visit is not performed for other participants.",
        "8.4  Reporting of laboratory results. Within 3 business days after each infusion visit, the site telephones the "
        "participant to report the laboratory results of that visit. Saturdays, Sundays and national holidays are not business days.",
        "8.5  Missed infusion visits. An infusion visit that cannot be performed within its allowable range is treated as missed "
        "and is not made up; the subsequent infusion visits remain as originally scheduled. The safety laboratory tests of the "
        "missed visit are performed additionally at the next infusion visit (that is, the tests are performed twice-timed: the "
        "missed pre-infusion sample and the regular pre-infusion sample are both collected before that infusion).",
        "8.6  If two consecutive infusion visits are missed, the participant is withdrawn from study treatment. No further "
        "infusion visits or home nursing visits are performed; only the final visit (8.7) is performed.",
        "8.7  Final visit. The final visit is performed on the first Monday on or after Day 210. Vital signs, body weight, safety "
        "laboratory tests, the MG-ADL score and an electrocardiogram are assessed.",
        "8.8  Unscheduled visit for fever. If the participant reports a body temperature of 38.5 degrees Celsius or higher on two "
        "consecutive days, the participant attends an unscheduled visit at the study site within 24 hours of the report, at which "
        "vital signs, hematology and blood cultures are obtained.",
        "8.9  Dose. The dose of KMT-22 is 5 mg/kg, calculated from the body weight measured at that infusion visit.",
    ):
        pdf.p(clause)
    pdf.save(path)


DATES = [{"what": ["day 1", "first infusion", "infusion 1", "start", "screening", "baseline"], "date": D1, "all": True}]
FAR = lambda yes: [{"what": ["50 km", "distance", "residence", "far", "home nurs*", "km"], "answer": yes, "optional": True},  # noqa: E731
                   {"what": ["50 km", "distance", "residence", "far", "home nurs*", "km"], "answer": ["more than 50", ">50", "over 50", "far"] if yes else ["50 km or less", "within 50", "<=50", "near"], "optional": True}]
mon = lambda n: [f"month {n}", f"m{n}", f"infusion {n + 1}", f"mo {n}"]  # noqa: E731
LABS, INF = has("Safety laboratory tests", "laborator*", "hematolog*", "biochem*"), has("KMT-22 infusion", "infusion", "kmt")
HOME = [["home"], ["nurs*"]]
CALL = [["laboratory results"], ["lab results"], ["result*", "telephone"], ["result*", "call"], ["report*", "result*"]]
FLAGS = [
    {"name": "8.2 calendar months on the same day of the month (not a fixed number of days)",
     "any": [["calendar month*"], ["same day of the month"], ["last day of"], ["month*", "30 days"]]},
    {"name": "8.4 three business days", "any": [["business day*"], ["working day*"], ["holiday*"]]},
    {"name": "8.5 laboratory tests of a missed visit are added to the next visit", "any": [["missed", "next"], ["missed", "laborator*"], ["missed", "additional*"]]},
    {"name": "8.6 two consecutive missed infusion visits: withdrawn from treatment", "any": [["two consecutive", "missed"], ["consecutive", "missed"], ["2 consecutive"]]},
    {"name": "8.7 final visit on the first Monday on or after Day 210", "any": [["monday"], ["weekday"]]},
]


def expected() -> dict[str, Any]:
    return {"fixture": ID, "constructed": True, "cases": [
        {"name": "A: lives 80 km away; nothing missed",
         "given": {"today": D1, "dates": DATES, "facts": FAR(True)},
         "expect": {"visits": [
             ev("Day 1 infusion", ["day 1", "d1", "infusion 1", "first infusion"], D1, has=[LABS, INF]),
             *[ev(f"Month {i + 1} infusion (same day of the calendar month)", mon(i + 1), MONTHS[i], [d(MONTHS[i], -5), d(MONTHS[i], 5)], has=[LABS, INF]) for i in range(6)],
             ev("Home nursing visit 3 days after the Day 1 infusion", HOME, d(D1, 3), [d(D1, 2), d(D1, 4)], modality="home"),
             ev("Home nursing visit 3 days after the Month 1 infusion", HOME, d(MONTHS[0], 3), [d(MONTHS[0], 2), d(MONTHS[0], 4)], modality="home"),
             ev("Telephone report of results within 3 business days after Day 1 (Sat 31 Jan: by Wed 4 Feb)", CALL, date_in=["2026-02-02", "2026-02-04"]),
             ev("Final visit (first Monday on or after Day 210)", ["final", "end of study", "eos"], FINAL, has=[LABS, has("Electrocardiogram", "ecg", "electrocardiogra*")], lacks=[INF]),
         ], "flags": FLAGS}},
        {"name": "B: lives near the site: no home nursing visits",
         "given": {"today": D1, "dates": DATES, "facts": FAR(False)},
         "expect": {"visits": [ev("Day 1 infusion", ["day 1", "d1", "infusion 1", "first infusion"], D1)],
                    "absent": [{"name": "Home nursing visit", "match": HOME}]}},
        {"name": "C: fever on two consecutive days reported on 10 Mar: unscheduled visit within 24 hours",
         "given": {"today": "2026-03-10", "dates": DATES, "facts": FAR(False), "events": [{"what": [["fever"], ["temperature"], ["unscheduled"]], "date": "2026-03-10"}]},
         "expect": {"visits": [ev("Unscheduled visit for fever", [["fever"], ["unscheduled"]], date_in=["2026-03-10", "2026-03-11"],
                                  has=[has("Blood cultures", "culture*"), has("Hematology", "hematolog*", "haematolog*")])]}},
    ]}


# Expectations the kit vocabulary could not meet on 10 November 2026: the calendar months, the
# Month 1 home visit that follows them, the business-day call and the Monday final visit. Since
# 18 November 2026 the hand model meets them with pattern rules (CALENDAR_MONTHS, BUSINESS_DAYS,
# WEEKDAY; app/kit/patterns.py). The missed-visit rules (8.5, 8.6) remain blockers: no handler yet.
KNOWN_ENGINE_GAPS: list[str] = []


def hand_model() -> dict[str, Any]:
    far = {"question": "far", "in": ["yes"]}
    pre = ["Vital signs", "Body weight", "Safety laboratory tests (hematology, biochemistry)", "MG-ADL score", "KMT-22 infusion"]
    return model(TITLE, "KMT-22-03",
        anchors=[anchor("day1", "start", "Day 1 (first infusion)", is_day1=True, set_by_visit="inf1")],
        questions=[question("far", "More than 50 km", "Does the participant live more than 50 km from the site?")],
        visits=[
            visit("inf1", "D1", "Day 1 infusion", "Treatment", "day1", 0, procedures=pre),
            # The vocabulary has days only: a calendar month is written as 30 days, which is not what 8.2 says.
            visit("infm", "M1", "Month 1 infusion", "Treatment", "day1", 30, (5, 5), pre,
                  repeat=repeat(30, 6, label_pattern="Month {n} infusion", code_pattern="M{n}")),
            visit("home1", "HN", "Home nursing visit", "Treatment", "day1", 3, (1, 1), ["Vital signs", "Infusion site check"], modality="home",
                  include_if=[far]),
            visit("homem", "HN-M", "Home nursing visit", "Treatment", "day1", 3, (1, 1), ["Vital signs", "Infusion site check"], modality="home",
                  include_if=[far], follows="infm", repeat=repeat(30, 6, label_pattern="Home nursing visit after Month {n}", code_pattern="HN-M{n}")),
            visit("final", "FIN", "Final visit", "Follow-up", "day1", 209, procedures=[
                "Vital signs", "Body weight", "Safety laboratory tests (hematology, biochemistry)", "MG-ADL score", "Electrocardiogram"]),
            # 8.4: the results call, counted in business days from each infusion visit.
            visit("call1", "CALL", "Telephone report of laboratory results", "Treatment", "day1", 3, (0, 0),
                  ["Report laboratory results"], modality="phone"),
            visit("callm", "CALL-M", "Telephone report of laboratory results", "Treatment", "day1", 3, (0, 0),
                  ["Report laboratory results"], modality="phone", follows="infm",
                  repeat=repeat(30, 6, label_pattern="Telephone report of laboratory results after Month {n}", code_pattern="CALL-M{n}")),
        ],
        pattern_rules=[
            {"id": "months", "pattern": "CALENDAR_MONTHS", "label": "Monthly infusions on the same day of the month",
             "visits": ["infm"], "params": {"months": 1, "every_months": 1, "missing_day": "last_day_of_month"},
             "quote": "If that day does not exist in a month, the infusion visit is scheduled on the last day of that month.", "page": 2},
            {"id": "call", "pattern": "BUSINESS_DAYS", "label": "Results call within 3 business days after each infusion",
             "visits": ["call1", "callm"], "params": {"business_days": 3, "within": True, "excludes_holidays": True},
             "quote": "Within 3 business days after each infusion visit, the site telephones the participant", "page": 2},
            {"id": "monday", "pattern": "WEEKDAY", "label": "Final visit on the first Monday on or after Day 210",
             "visits": ["final"], "params": {"weekday": "monday", "direction": "on_or_after"},
             "quote": "The final visit is performed on the first Monday on or after Day 210.", "page": 2},
            {"id": "carry", "pattern": "MISSED_VISIT_TESTS_CARRIED_OVER", "label": "Missed visit's laboratory tests added to the next visit",
             "visits": ["infm"], "params": {"tests": ["Safety laboratory tests"]},
             "quote": "The safety laboratory tests of the missed visit are performed additionally at the next infusion visit", "page": 2},
            {"id": "two_missed", "pattern": "CONSECUTIVE_MISSED_VISITS_WITHDRAW", "label": "Two consecutive missed infusions: withdrawn",
             "visits": ["infm", "homem", "callm"], "params": {"consecutive": 2, "keeps_visits": ["final"]},
             "quote": "If two consecutive infusion visits are missed, the participant is withdrawn from study treatment.", "page": 2},
        ],
        events=[event("fever", "Fever of 38.5 C or more on two consecutive days", repeatable=True, visits=[
            visit("fever_visit", "UNS", "Unscheduled visit for fever", "Unscheduled", "day1", 0, (0, 1), ["Vital signs", "Hematology", "Blood cultures"])])],
        notes=["Hand-written model: calendar months, the business-day results call and the Monday final visit are pattern rules; "
               "the missed-visit rules (8.5, 8.6) are pattern rules with no handler yet, so this model blocks enrolment."])
