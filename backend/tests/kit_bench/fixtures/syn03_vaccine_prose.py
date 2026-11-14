"""SYN-03: two-dose vaccine study numbered from Day 0, described in prose only (no schedule table).

Constructed fixture, not a real protocol. Style: investigator-initiated academic protocol, plain
numbered paragraphs.

Rules it carries:
- Day 0 = first vaccination (the protocol numbers days from Day 0). Screening on Day 0 or up to 14 days before.
- Telephone contact on Day 7 (+/-1).
- Second vaccination on Day 28; it may be given up to 7 days later but never before Day 28.
- Everything after the second vaccination counts from the date it was actually given: telephone
  contact 7 days after (+/-1), clinic visit 28 days after (+/-3), final visit 180 days after (+/-14).
- Diary for 7 days starting on the day of each vaccination.
- Urine pregnancy test before each vaccination, only for participants who can become pregnant.
- Group B (assigned at enrolment): a third vaccination at the 180-day visit, then a telephone
  contact 7 days later (+/-1) and the final visit 28 days later (+/-3).
- Second vaccination not given (decided at the Day 28 visit): no post-vaccination-2 contacts;
  safety visits on Day 56 (+/-3) and Day 208 (+/-14) counted from Day 0.
"""

from __future__ import annotations

from typing import Any

from ..common import anchor, d, ev, has, model, proc, question, visit, win
from ..pdfgen import Pdf

ID = "syn03-vaccine-prose"
TITLE = "RSV-PV-07: two-dose vaccine study, Day 0 numbering, schedule in prose only"
COVERS = ["prose-only schedule (no table)", "Day 0 convention", "one-sided window (not earlier)", "visits counted from an actual earlier visit",
          "diary periods", "arm-specific extra visits", "decision at a visit switches the follow-up plan",
          "sex-conditional assessment"]

D0 = "2026-03-02"


def build_pdf(path: str) -> None:
    pdf = Pdf(header="RSV-PV-07 Protocol v1.4 (02 Feb 2026)", footer="University Vaccine Research Unit", font="tiro", bold="tibo", size=11)
    pdf.title(["RSV-PV-07", "A randomised, observer-blind study of the safety and immunogenicity of two doses of the PreF-07 "
               "vaccine in healthy adults aged 18 to 49 years, with a booster cohort",
               "University Vaccine Research Unit", "Protocol version 1.4, 02 February 2026"])
    pdf.new_page()
    pdf.h1("4. Study design")
    pdf.p("4.1  Participants receive two intramuscular doses of PreF-07 or placebo. At enrolment each participant is "
          "allocated to Group A (two doses) or Group B (two doses and a booster dose). The group is known to the site "
          "from enrolment onwards.")
    pdf.p("4.2  Study days are numbered from the day of the first vaccination, which is Day 0. Screening may take place "
          "on Day 0 before vaccination or on any of the 14 days before Day 0.")
    pdf.h1("7. Visits and procedures")
    pdf.p("7.1  Day 0 (clinic). After eligibility has been confirmed, a blood sample for immunogenicity is taken and "
          "the first vaccination is given. Participants who can become pregnant have a urine pregnancy test before "
          "vaccination. Participants are observed for 30 minutes. A diary card is issued; participants record "
          "solicited reactions every day for 7 days starting on the day of vaccination.")
    pdf.p("7.2  Day 7 (telephone, window plus or minus 1 day). The study nurse reviews the diary card and any adverse "
          "events with the participant.")
    pdf.p("7.3  Day 28 (clinic). A blood sample for immunogenicity is taken, followed by the second vaccination, a "
          "30-minute observation period and a new diary card for 7 days starting on the day of vaccination. "
          "Participants who can become pregnant have a urine pregnancy test before vaccination. The second "
          "vaccination must not be given before Day 28; it may be given up to 7 days after Day 28.")
    pdf.p("7.4  All contacts after the second vaccination are timed from the date on which the second vaccination was "
          "actually given, not from Day 0:")
    pdf.bullets(["7 days after the second vaccination (plus or minus 1 day): telephone review of the diary card and adverse events;",
                 "28 days after the second vaccination (plus or minus 3 days): clinic visit with a blood sample for immunogenicity and review of adverse events;",
                 "180 days after the second vaccination (plus or minus 14 days): clinic visit with a blood sample for immunogenicity. For Group A this is the final study visit."],
                mark="-", indent=22)
    pdf.p("7.5  Group B only. At the visit 180 days after the second vaccination, after the blood sample, Group B "
          "participants receive the booster vaccination (with a urine pregnancy test beforehand for participants who "
          "can become pregnant), are observed for 30 minutes and complete a diary card for 7 days starting that day. "
          "They are telephoned 7 days after the booster (plus or minus 1 day) and attend a final study visit 28 days "
          "after the booster (plus or minus 3 days), at which a blood sample for immunogenicity is taken.")
    pdf.p("7.6  Participants who do not receive the second vaccination. Whether the second vaccination is given is "
          "decided at the Day 28 visit. A participant who does not receive it (for example because of pregnancy, a "
          "severe allergic reaction to the first dose, or the participant's own decision) is not withdrawn. The Day "
          "28 blood sample is still taken. The contacts described in 7.4 and 7.5 do not apply; instead the participant "
          "attends the clinic for safety review on Day 56 (plus or minus 3 days) and on Day 208 (plus or minus 14 "
          "days), both counted from Day 0. No blood samples are taken at these two visits and no diary card is used.")
    pdf.p("7.7  A participant who withdraws consent has no further visits.")
    pdf.save(path)


DATES = [{"what": ["day 0", "vaccination 1", "first vaccination", "dose 1", "enrol*", "screening"], "date": D0, "all": True}]
GROUP = lambda g: {"what": ["group", "cohort", "booster"], "answer": [f"group {g}", g] if g != "bool" else True}  # noqa: E731
PREG = lambda yes: {"what": ["pregnan*", "childbearing"], "answer": yes}  # noqa: E731
DOSE2 = lambda yes: {"what": ["second vaccination", "vaccination 2", "dose 2", "second dose"], "answer": yes, "optional": yes}  # noqa: E731

W = {"d0": ["day 0", "d0", "vaccination 1", "first vaccination", "dose 1"], "d7": ["day 7", "d7"],
     "d28": ["day 28", "d28", "vaccination 2", "second vaccination", "dose 2"],
     "p7": [["7 days after", "second"], ["7 days after", "vaccination 2"], ["7 days after", "dose 2"], ["day 35"], ["d35"], ["v2 7"], ["dose 2 7"]],
     "p28": [["28 days after", "second"], ["28 days after", "vaccination 2"], ["28 days after", "dose 2"], ["day 56"], ["d56"]],
     "p180": [["180 days after"], ["day 208"], ["d208"], ["6 month*"], ["month 6"], ["booster vaccination"], ["final"]],
     "b7": [["7 days after", "booster"], ["booster", "7"], ["booster", "telephone"], ["booster", "phone"]],
     "b28": [["28 days after", "booster"], ["booster", "28"], ["final"]]}
BLOOD, VACC = has("Immunogenicity blood sample", "immunogenicity", "blood sample", "serolog*"), has("Vaccination", "vaccin*")
UPT, DIARY = has("Urine pregnancy test", "pregnancy"), has("Diary card", "diary")


def after_dose2(dose2: str, pregnant_capable: bool, group_b: bool = False) -> list[dict[str, Any]]:
    out = [
        ev("Telephone contact 7 days after vaccination 2", W["p7"], d(dose2, 7), win(d(dose2, 7), 1, 1), modality="phone"),
        ev("Clinic visit 28 days after vaccination 2", W["p28"], d(dose2, 28), win(d(dose2, 28), 3, 3), modality="site", has=[BLOOD], lacks=[VACC]),
        ev("Visit 180 days after vaccination 2" + (" with booster (Group B)" if group_b else " (final for Group A)"), W["p180"], d(dose2, 180),
           win(d(dose2, 180), 14, 14), has=[BLOOD, *([VACC] if group_b else []), *([UPT] if group_b and pregnant_capable else [])],
           lacks=[] if group_b else [VACC, UPT]),
    ]
    if group_b:
        out += [ev("Telephone contact 7 days after the booster", W["b7"], d(dose2, 187), win(d(dose2, 187), 1, 1), modality="phone"),
                ev("Final visit 28 days after the booster", W["b28"], d(dose2, 208), win(d(dose2, 208), 3, 3), has=[BLOOD], lacks=[VACC])]
    return out


def first_part(pregnant_capable: bool, dose2_given: bool = True) -> list[dict[str, Any]]:
    return [
        ev("Day 0", W["d0"], D0, has=[BLOOD, VACC, DIARY, *([UPT] if pregnant_capable else [])], lacks=[] if pregnant_capable else [UPT]),
        ev("Day 7 telephone contact", W["d7"], d(D0, 7), win(d(D0, 7), 1, 1), modality="phone", lacks=[BLOOD, VACC]),
        ev("Day 28", W["d28"], d(D0, 28), [d(D0, 28), d(D0, 35)], has=[BLOOD, *([VACC] if dose2_given else [])],
           lacks=[] if dose2_given else [VACC]),
    ]


def expected() -> dict[str, Any]:
    late = d(D0, 33)
    return {"fixture": ID, "constructed": True, "cases": [
        {"name": "A: Group A, can become pregnant, both vaccinations on time",
         "given": {"today": D0, "dates": DATES, "facts": [GROUP("A"), PREG(True), DOSE2(True)]},
         "expect": {"visits": [*first_part(True), *after_dose2(d(D0, 28), True)],
                    "absent": [{"name": "Booster contacts (Group B only)", "match": W["b7"]}]}},
        {"name": "B: Group A, vaccination 2 given on Day 33: later contacts count from that date",
         "given": {"today": late, "dates": DATES, "facts": [GROUP("A"), PREG(False), DOSE2(True)],
                   "actuals": [{"visit": W["d0"], "date": D0}, {"visit": W["d28"], "date": late, "all": True}]},
         "expect": {"visits": after_dose2(late, False)}},
        {"name": "C: Group B on time: booster at the 180-day visit, then 7 and 28 days after it",
         "given": {"today": D0, "dates": DATES, "facts": [GROUP("B"), PREG(True), DOSE2(True)]},
         "expect": {"visits": [*first_part(True), *after_dose2(d(D0, 28), True, group_b=True)]}},
        {"name": "D: Group A, vaccination 2 not given: safety visits on Day 56 and Day 208 from Day 0, no post-dose contacts",
         "given": {"today": d(D0, 28), "dates": DATES, "facts": [GROUP("A"), PREG(True), {**DOSE2(False), "optional": False}],
                   "actuals": [{"visit": W["d0"], "date": D0}, {"visit": W["d28"], "date": d(D0, 30), "all": True}]},
         "expect": {"visits": [
             ev("Day 56 safety visit (counted from Day 0)", [["day 56"], ["d56"], ["safety"]], d(D0, 56), win(d(D0, 56), 3, 3), modality="site", lacks=[BLOOD, VACC]),
             ev("Day 208 safety visit (counted from Day 0)", [["day 208"], ["d208"], ["safety"]], d(D0, 208), win(d(D0, 208), 14, 14), lacks=[BLOOD, VACC])],
             "absent": [{"name": "Telephone contact 7 days after a vaccination that was not given", "match": W["p7"], "after": d(D0, 28)},
                        {"name": "Visit 28 days after vaccination 2 (date counted from the Day 28 visit)", "match": W["p28"], "on": d(D0, 58)}]}},
    ]}


def hand_model() -> dict[str, Any]:
    preg = {"question": "can_be_pregnant", "in": ["yes"]}
    given, not_given = {"question": "dose2_given", "in": ["yes"]}, {"question": "dose2_given", "in": ["no"]}
    b = {"question": "group", "in": ["B"]}
    return model(TITLE, "RSV-PV-07",
        anchors=[
            anchor("day0", "start", "Day 0 (first vaccination)", is_day1=True, day_zero=True, set_by_visit="d0"),
            anchor("dose2", "relative", "Second vaccination", relative_to="day0", offset_days=28, set_by_visit="d28"),
            anchor("booster", "relative", "Booster vaccination", relative_to="dose2", offset_days=180, set_by_visit="p180"),
        ],
        questions=[
            question("group", "Group", "Which group?", kind="choice", options=[("A", "Group A"), ("B", "Group B (booster)")]),
            question("can_be_pregnant", "Can become pregnant", "Can the participant become pregnant?"),
            question("dose2_given", "Second vaccination", "Was the second vaccination given?", scope="decision", ask_at={"visit": "d28", "n": None}),
        ],
        visits=[
            visit("d0", "D0", "Day 0: first vaccination", "Vaccination", "day0", 0, procedures=[
                "Eligibility", "Immunogenicity blood sample", proc("Urine pregnancy test", preg), "Vaccination 1", "30-minute observation", "Diary card issued"],
                duration_days=None),
            visit("d7", "D7", "Day 7 telephone contact", "Vaccination", "day0", 7, (1, 1), ["Diary card review", "Adverse events"], modality="phone"),
            visit("d28", "D28", "Day 28: second vaccination", "Vaccination", "day0", 28, (0, 7), [
                "Immunogenicity blood sample", proc("Urine pregnancy test", preg, given), proc("Vaccination 2", given),
                proc("30-minute observation", given), proc("Diary card issued", given)]),
            visit("p7", "V2+7", "Telephone contact 7 days after vaccination 2", "Follow-up", "dose2", 7, (1, 1), ["Diary card review", "Adverse events"],
                  modality="phone", include_if=[given]),
            visit("p28", "V2+28", "Clinic visit 28 days after vaccination 2", "Follow-up", "dose2", 28, (3, 3), ["Immunogenicity blood sample", "Adverse events"],
                  include_if=[given]),
            visit("p180", "V2+180", "Visit 180 days after vaccination 2", "Follow-up", "dose2", 180, (14, 14), [
                "Immunogenicity blood sample", proc("Urine pregnancy test", preg, b), proc("Booster vaccination", b), proc("30-minute observation", b),
                proc("Diary card issued", b)], include_if=[given]),
            visit("b7", "B+7", "Telephone contact 7 days after the booster", "Booster", "booster", 7, (1, 1), ["Diary card review", "Adverse events"],
                  modality="phone", include_if=[given, b]),
            visit("b28", "B+28", "Final visit 28 days after the booster", "Booster", "booster", 28, (3, 3), ["Immunogenicity blood sample", "Adverse events"],
                  include_if=[given, b]),
            visit("s56", "D56", "Day 56 safety visit", "Safety follow-up", "day0", 56, (3, 3), ["Safety review", "Adverse events"], include_if=[not_given]),
            visit("s208", "D208", "Day 208 safety visit", "Safety follow-up", "day0", 208, (14, 14), ["Safety review", "Adverse events"], include_if=[not_given]),
        ],
        events=[{"id": "withdraw", "label": "Withdrawal of consent", "repeatable": False, "ends_schedule": True, "sets_anchor": None, "note": None,
                 "questions": [], "visits": []}])
