"""Write kit-expected.json: the BP11-301 expected answer in the form the kit bench compares.

    python docs/protocol-benchmarks/bp11-301-v2.1/build_kit_expected.py

Everything here comes from oracle-draft.json and expected-behavior.md (written from the protocol
before any comparison with the app). This script only restates them without the app's ids: visit
dates and windows from the oracle's ``visits``, one has/lacks check per cell of its 26 x 12
``assessment_matrix``, and the scheduling scenarios (CAL, DOSE, TIME, STOP, ET, COND, PREG). The
expected answer is an AI-written draft: its open questions stay open and are checked as "the app
must raise this with the reviewer", never as a value the app must produce.

Scenarios about identity, diary scoring, blinding, audit history, rescreening identifiers and safety
reporting deadlines are outside a visit schedule and are not compared (listed under "not_compared").
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent
oracle = json.loads((HERE / "oracle-draft.json").read_text(encoding="utf-8"))

# Words that find each visit on a schedule (visit number -> alternatives).
VISIT_WORDS = {
    1: ["screening", "scr", "visit 1"],
    2: ["diary period", "week 1", "w 1", "visit 2", ["diary", "baseline"], ["diary", "pre*"]],
    3: ["week 0", "w0", "baseline", "day 1", "visit 3"],
    4: ["week 2", "w2", "visit 4"],
    5: ["week 4", "w4", "visit 5"],
    6: ["week 8", "w8", "visit 6"],
    7: ["week 12", "w12", "visit 7"],
    8: ["week 16", "w16", "visit 8"],
    9: ["week 20", "w20", "visit 9"],
    10: ["week 24", "w24", "visit 10"],
    11: ["week 32", "w32", "visit 11"],
    12: ["week 40", "w40", "visit 12"],
}
VISIT_NAMES = {1: "Screening", 2: "Pre-baseline diary period", 3: "Week 0 / Day 1", 4: "Week 2", 5: "Week 4", 6: "Week 8",
               7: "Week 12", 8: "Week 16", 9: "Week 20", 10: "Week 24", 11: "Week 32 (telephone)", 12: "Week 40 / EOS"}

# Words that find each assessment row (row label prefix -> [(name, alternatives, marked-visits override, conditional)]).
ROWS = {
    "Informed consent": [("Informed consent", ["informed consent", "consent"], None, None)],
    "Inclusion/exclusion": [("Inclusion/exclusion criteria", ["inclusion", "eligibility criteria", "entry criteria"], None, None)],
    "Demographics": [("Demographics", ["demograph*"], None, None)],
    "Medical history": [("Medical history", ["medical history"], None, None)],
    # Footnote a and the narrative: height at screening only; weight where the table marks it.
    "Height and weight": [("Height", ["height"], [1], None), ("Weight", ["weight"], None, None)],
    "Physical examination": [("Physical examination", ["physical exam*"], None, None)],
    "Viral serology": [("Viral serology", ["serolog*", "hepatitis", "hiv"], None, None)],
    "Vital signs": [("Vital signs", ["vital*"], None, None)],
    "12-Lead ECG": [("12-lead ECG", ["ecg", "electrocardiogra*"], None, None)],
    "Hematology": [("Hematology", ["hematolog*", "haematolog*"], None, None),
                   # expected-behavior.md: coagulation only at screening, Week 12 and Week 24.
                   ("Coagulation", ["coagulat*"], [1, 7, 10], None)],
    "Serum chemistry": [("Serum chemistry", ["chemistry"], None, None)],
    "Urinalysis": [("Urinalysis", ["urinalysis"], None, None)],
    "Stool ova": [("Stool ova and parasite", ["stool"], None, None)],
    "Randomization": [("Randomization", ["randomi*"], None, None)],
    "Rerandomization": [("Rerandomization", ["rerandomization", "rerandomisation", "re randomization", "re randomisation"], None, None)],
    "Study treatment administration": [("Study treatment administration",
                                        ["study treatment", "study drug", "treatment administration", "subcutaneous inject*", "dosing"], None, None)],
    "Injection-site": [("Injection-site / hypersensitivity reaction", ["injection site", "hypersensitiv*"], None, None)],
    "Patient": [("Patient e-diary", ["diary", "ediary"], None, None)],
    "DLQI": [("DLQI", ["dlqi", "dermatology life quality"], None, None)],
    "Pregnancy test": [("Pregnancy test", ["pregnancy"], None, True)],
    "COVID-19": [("COVID-19 test", ["covid", "sars"], None, None)],
    "PK and PD": [("PK sampling", ["pk", "pharmacokinetic*"], None, None)],
    "Immunogenicity": [("Immunogenicity sampling", ["immunogenicity", "anti drug", "ada"], None, None)],
    "AE/SAE": [("AE/SAE", ["ae", "aes", "adverse event*", "sae"], None, None)],
    "Concomitant": [("Concomitant medication", ["concomitant", "conmed*"], None, None)],
    "Telephonic": [("Telephonic follow-up", ["telephon*", "phone"], None, None)],
}


def matrix() -> dict[int, dict[str, list]]:
    cells: dict[int, dict[str, list]] = {n: {"has": [], "lacks": []} for n in range(1, 13)}
    for row in oracle["assessment_matrix"]:
        key = next(k for k in ROWS if row["row_label"].startswith(k))
        for name, words, override, conditional in ROWS[key]:
            marked = override or row["marked_visits"]
            for n in range(1, 13):
                if n in marked:
                    item: dict = {"name": name, "any": words}
                    if conditional is not None:
                        item["conditional"] = conditional
                    cells[n]["has"].append(item)
                else:
                    cells[n]["lacks"].append({"name": name, "any": words})
    return cells


CELLS = matrix()
SCREENING = "2025-12-04"   # 28 days before baseline: the longest screening the protocol allows
BASELINE = oracle["baseline_example"]
DATES = [{"what": ["screening", "consent", "first visit"], "date": SCREENING},
         {"what": ["day 1", "baseline", "randomi*", "week 0", "first dose"], "date": BASELINE}]
ELIGIBLE = {"what": ["eligible", "eligibility", "randomi*", "inclusion"], "answer": True, "optional": True, "all": True}
WEEK12 = {"what": ["week 12", "rerandomi*", "tp2"], "answer": ["bp11", "continu*", "rerandomi*", "yes"], "optional": True}
ARM = {"what": ["arm", "treatment", "assigned", "dose group"], "answer": ["300", "a"], "optional": True}
FOCBP = {"what": ["childbearing", "focbp", "wocbp"], "answer": True}
NOT_FOCBP = {"what": ["childbearing", "focbp", "wocbp"], "answer": False}
RISK = {"what": ["parasit*"], "answer": True}
EOS_HIGH = {"what": ["eosinophil*"], "answer": 2.1}


def visit(n: int, with_cells: bool = True, **over) -> dict:
    v = next(x for x in oracle["visits"] if x["visit_number"] == n)
    out: dict = {"name": VISIT_NAMES[n], "match": VISIT_WORDS[n]}
    if n == 1:
        out["date"] = SCREENING  # the date the site entered; the protocol gives a range, not a target
    elif n >= 3:
        out["date"] = v["target_date"]
        out["window"] = [v["earliest"], v["latest"]] if v["window_days"] is not None else None
    if n == 11:
        out["modality"] = "phone"
    if with_cells:
        out["has"], out["lacks"] = CELLS[n]["has"], CELLS[n]["lacks"]
    out.update(over)
    return out


# Things the protocol leaves open or contradicts itself on (expected-behavior.md, "Source conflicts
# that the app must expose"; oracle open_questions). The app must raise each with the reviewer.
FLAGS = [
    {"name": "Q01 negative days / diary Day -7 to Day 0 convention", "any": [["day 0"], ["day 7", "diary"], ["negative", "day"]]},
    {"name": "Q02 diary repeat extends screening beyond four weeks (approval, no stated maximum)",
     "any": [["repeat", "diary"], ["diary", "beyond four weeks"], ["diary", "extend*"]]},
    {"name": "Q03 weight: table marks some visits, narrative says whenever vital signs are taken", "any": [["weight", "vital"]]},
    {"name": "Q04 COVID-19 test: conditional footnote vs unconditional screening bullet", "any": [["covid"]]},
    {"name": "Q05 PK/PD at Weeks 24 and 40 have no dose to be 'within two hours before'",
     "any": [["pk", "week 24"], ["pk", "week 40"], ["predose", "week 24"], ["pk", "no dose"], ["pk", "non dosing"], ["pk", "nondosing"],
             ["sampl*", "week 24", "dos*"], ["sampl*", "no dose"]]},
    {"name": "Q06 normal EOS: Week 40 / Day 281 vs twenty weeks after the last dose",
     "any": [["week 40", "last dose"], ["eos", "last dose"], ["20 weeks", "last"], ["twenty weeks", "last"], ["delayed dose"], ["late dose"]]},
    {"name": "ET01 early-termination EOS (4 weeks after last dose) vs remaining safety follow-up",
     "any": [["early", "eos", "week 40"], ["early termination", "week 40"], ["early", "eos", "follow up"], ["4 week", "eos", "week 40"]]},
    {"name": "Q10 diary collection up to Week 24 without a stated end time or count",
     "any": [["diary", "week 24"], ["diary", "until"], ["diary", "end"]]},
]

cases = [
    {
        "name": "CAL01 nominal schedule (FOCBP, parasite risk, eosinophils 2.1 x ULN)",
        "given": {"today": SCREENING, "dates": DATES, "facts": [FOCBP, RISK, EOS_HIGH, ARM, ELIGIBLE, WEEK12]},
        "expect": {
            "visits": [visit(1, has=[*CELLS[1]["has"], {"name": "Serum pregnancy test at screening (PREG01)", "any": [["serum", "pregnancy"]]}]),
                       visit(2), *[visit(n) for n in range(3, 13)]],
            "no_other_visits": True, "allow_extra": ["dose", "treatment", "administration", "injection*"],
            "flags": FLAGS,
        },
    },
    {
        "name": "CAL05 no baseline date yet",
        "given": {"today": SCREENING, "dates": [DATES[0]], "facts": [FOCBP]},
        "expect": {"visits": [visit(1, with_cells=False), *[{"name": VISIT_NAMES[n], "match": VISIT_WORDS[n], "date": None} for n in (4, 7, 12)]]},
    },
    {
        "name": "CAL02 Week 4 treatment given late (1 Feb): later visits keep the protocol grid",
        "given": {"today": "2026-02-02", "dates": DATES, "facts": [FOCBP, ARM, ELIGIBLE, WEEK12],
                  "actuals": [{"visit": VISIT_WORDS[3], "date": BASELINE, "all": True}, {"visit": VISIT_WORDS[5], "date": "2026-02-01", "all": True}]},
        "expect": {"visits": [visit(n, with_cells=False) for n in range(6, 13)],
                   "flags": [{"name": "CAL02 handling of a late dose is not stated",
                              "any": [["delayed dose"], ["late dose"], ["dose", "shift*"], ["does not state", "delay*"], ["not state", "dose"]]}]},
    },
    {
        "name": "TIME01-04 intraday rules at a dosing visit",
        "given": {"today": SCREENING, "dates": DATES, "facts": [FOCBP, ARM, ELIGIBLE, WEEK12]},
        "expect": {"visits": [
            {"name": "Week 4", "match": VISIT_WORDS[5], "date": "2026-01-29",
             "says": [{"name": "PK/PD within two hours before planned dosing (footnote j)",
                       "any": [["2", "h"], ["two hours"], ["2 hours"], ["hours", "2"], ["120"], ["within 2"]]},
                      {"name": "post-administration observation of at least 60 minutes",
                       "any": [["60 min*"], ["60"], ["1 hour"], ["one hour"]]}]},
            {"name": "Week 24 (no dose)", "match": VISIT_WORDS[10], "date": "2026-06-18",
             "lacks": [{"name": "an invented Week 24 dose (TIME07)", "any": ["study treatment", "study drug", "subcutaneous inject*"]}]},
        ]},
    },
    {
        "name": "STOP01 treatment stopped 10 Feb, before Week 12 rerandomization (last dose given 29 Jan)",
        "given": {"today": "2026-02-10", "dates": DATES, "facts": [FOCBP, ARM, ELIGIBLE],
                  "actuals": [{"visit": VISIT_WORDS[3], "date": BASELINE, "all": True}, {"visit": VISIT_WORDS[5], "date": "2026-01-29", "all": True}],
                  "events": [{"what": [["treatment", "discontinu*"], ["stop*", "treatment"], ["treatment", "stop*"], ["discontinu*", "study drug"]],
                              "date": "2026-02-10",
                              "answers": [{"what": ["rerandomi*", "week 12"], "answer": False, "optional": True}]}]},
        "expect": {
            "visits": [
                {"name": "Week 8 (efficacy and safety follow-up continue)", "match": VISIT_WORDS[6], "date": "2026-02-26",
                 "lacks": [{"name": "study treatment after it was stopped", "any": ["study treatment", "study drug", "subcutaneous inject*"]}]},
                {"name": "Week 12 (last efficacy follow-up)", "match": VISIT_WORDS[7], "date": "2026-03-26",
                 "has": [{"name": "Patient e-diary", "any": ["diary"]}],
                 "lacks": [{"name": "study treatment after it was stopped", "any": ["study treatment", "study drug", "subcutaneous inject*"]},
                           {"name": "rerandomization of a patient no longer on treatment", "any": ["rerandomization", "rerandomisation", "re randomization"]}]},
                {"name": "Week 16 (safety follow-up only)", "match": VISIT_WORDS[8], "date": "2026-04-23",
                 "has": [{"name": "AE/SAE", "any": ["ae", "adverse event*", "sae"]}],
                 "lacks": [{"name": "study treatment after it was stopped", "any": ["study treatment", "study drug", "subcutaneous inject*"]},
                           {"name": "efficacy follow-up (DLQI) after Week 12", "any": ["dlqi"]},
                           {"name": "efficacy follow-up (e-diary) after Week 12", "any": ["diary"]}]},
                {"name": "Week 24 (safety and immunogenicity follow-up)", "match": VISIT_WORDS[10], "date": "2026-06-18",
                 "has": [{"name": "Immunogenicity sampling", "any": ["immunogenicity", "anti drug", "ada"]}]},
                {"name": "Week 40 / EOS (follow-up through EOS is kept)", "match": VISIT_WORDS[12], "date": "2026-10-08"},
                {"name": "ET01 early-termination EOS, 4 weeks after the last dose actually given",
                 "match": [["early", "eos"], ["early termination"], ["early", "end of study"], ["et"], ["eot"]],
                 "date": "2026-02-26", "window": ["2026-02-19", "2026-03-05"]},
            ],
            "flags": [FLAGS[6]],
        },
    },
    {
        "name": "STOP02 treatment stopped 1 Apr, after Week 12 rerandomization",
        "given": {"today": "2026-04-01", "dates": DATES, "facts": [FOCBP, ARM, ELIGIBLE, WEEK12],
                  "actuals": [{"visit": VISIT_WORDS[n], "date": d, "all": True} for n, d in
                              ((3, BASELINE), (5, "2026-01-29"), (6, "2026-02-26"), (7, "2026-03-26"))],
                  "events": [{"what": [["treatment", "discontinu*"], ["stop*", "treatment"], ["treatment", "stop*"], ["discontinu*", "study drug"]],
                              "date": "2026-04-01",
                              "answers": [{"what": ["rerandomi*", "week 12"], "answer": True, "optional": True}]}]},
        "expect": {"visits": [
            {"name": "Week 16 (efficacy, safety and immunogenicity follow-up continue)", "match": VISIT_WORDS[8], "date": "2026-04-23",
             "has": [{"name": "DLQI", "any": ["dlqi"]}, {"name": "Patient e-diary", "any": ["diary"]}],
             "lacks": [{"name": "study treatment after it was stopped", "any": ["study treatment", "study drug", "subcutaneous inject*"]}]},
            {"name": "Week 20", "match": VISIT_WORDS[9], "date": "2026-05-21",
             "lacks": [{"name": "study treatment after it was stopped", "any": ["study treatment", "study drug", "subcutaneous inject*"]}]},
            {"name": "Week 40 / EOS", "match": VISIT_WORDS[12], "date": "2026-10-08"},
        ]},
    },
    {
        "name": "ET02 treatment stopped with no dose date recorded: early EOS waits for the actual last dose",
        "given": {"today": "2026-02-10", "dates": DATES, "facts": [FOCBP, ARM, ELIGIBLE],
                  "events": [{"what": [["treatment", "discontinu*"], ["stop*", "treatment"], ["treatment", "stop*"], ["discontinu*", "study drug"]],
                              "date": "2026-02-10", "answers": [{"what": ["rerandomi*", "week 12"], "answer": False, "optional": True}]}]},
        "expect": {"visits": [{"name": "Early-termination EOS (no recorded dose to count from)",
                               "match": [["early", "eos"], ["early termination"], ["early", "end of study"], ["et"], ["eot"]],
                               "date": None}]},
    },
    {
        "name": "COND02 eosinophils 2.1 x ULN but no parasite risk: no stool test",
        "given": {"today": SCREENING, "dates": DATES, "facts": [NOT_FOCBP, {"what": ["parasit*"], "answer": False}, EOS_HIGH]},
        "expect": {"visits": [{"name": "Screening", "match": VISIT_WORDS[1], "date": SCREENING,
                               "lacks": [{"name": "Stool test (risk AND eosinophils > 2 x ULN)", "any": ["stool"]},
                                         {"name": "Pregnancy test for a patient not of childbearing potential", "any": ["pregnancy"]}]},
                              {"name": "Week 4", "match": VISIT_WORDS[5], "date": "2026-01-29",
                               "lacks": [{"name": "Pregnancy test for a patient not of childbearing potential", "any": ["pregnancy"]}]}]},
    },
    {
        "name": "COND03 parasite risk and eosinophils exactly 2.0 x ULN: no stool test (strictly greater)",
        "given": {"today": SCREENING, "dates": DATES, "facts": [NOT_FOCBP, RISK, {"what": ["eosinophil*"], "answer": 2.0}]},
        "expect": {"visits": [{"name": "Screening", "match": VISIT_WORDS[1], "date": SCREENING,
                               "lacks": [{"name": "Stool test at exactly 2 x ULN", "any": ["stool"]}]}]},
    },
    {
        "name": "COND04 parasite risk, eosinophil result not known yet: stool test stays undecided",
        "given": {"today": SCREENING, "dates": DATES, "facts": [NOT_FOCBP, RISK]},
        "expect": {"visits": [{"name": "Screening", "match": VISIT_WORDS[1], "date": SCREENING,
                               "has": [{"name": "Stool test shown as depending on the result", "any": ["stool"], "conditional": True}]}]},
    },
]

expected = {
    "source": "docs/protocol-benchmarks/bp11-301-v2.1 (oracle-draft.json, expected-behavior.md)",
    "status": oracle["status"], "real_protocol": True,
    "not_compared": ["ID01", "ID02", "CAL03 (window deviation is shown on the patient timeline, not in the model)", "TIME05", "TIME06",
                     "ORDER01", "W12_01", "W12_02", "DIARY01-03", "BLIND01", "IMMUT01", "AMEND01", "SAFETY01", "RESCREEN01",
                     "WITHDRAW01", "Q07 hives scale", "Q08 DLQI question 7", "Q09 clock boundaries"],
    "cases": cases,
}
(HERE / "kit-expected.json").write_text(json.dumps(expected, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"wrote kit-expected.json: {len(cases)} cases")
