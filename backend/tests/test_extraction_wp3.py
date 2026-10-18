"""Compiler -> WP3 operators: weekday calendar series and scoped processes.

Synthetic facts only. Each schedule is compiled, completed only where a site
must supply data (a holiday calendar), approved and evaluated by the real
engine. 2026-10-02 is a Friday; 2026-10-12 is the site's holiday.
"""

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _engine_cases import by_code, context, evaluate  # noqa: E402
from app.domain.schedule.models import (  # noqa: E402
    ConditionState, HolidayCalendar, PatientConditionStatus, ScheduleStatus, Weekday,
)
from app.domain.schedule.validator import ScheduleValidator  # noqa: E402
from app.extraction import ledger as L  # noqa: E402
from app.extraction.compiler import compile_schedule  # noqa: E402
from app.extraction.facts import DocumentFacts, ScheduleFact  # noqa: E402
from app.extraction.reader import ReaderLimits  # noqa: E402

import test_extraction_accountability as acc  # noqa: E402

FRIDAY = date(2026, 10, 2)
HOLIDAY = date(2026, 10, 12)
MON_FRI = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def fact(what, quote, *, fid, page=10, **values):
    values.setdefault("kind", "visit")
    return ScheduleFact.model_validate({
        "id": fid, "what": what, "evidence": [{"page": page, "quote": quote, "verification": "TEXT_MATCHED"}],
        **values})


def anchor_fact(name, fid):
    return fact(name, f"{name} is recorded", fid=fid, kind="anchor", anchor={"type": "RECORDED"})


def compile_facts(*facts, day_1, blocks=()):
    return compile_schedule(DocumentFacts(
        facts=list(facts), blocks=list(blocks),
        day_numbering={"day_1_anchor": day_1, "counting_convention": "CLINICAL_DAY"}))


def blocking(result):
    return sorted(issue.issue_code for issue in ScheduleValidator.blocking(result.issues))


def stated(schedule):
    """A reviewer EXPLICITLY states each recurrence basis the compiler could only propose.

    The compiler never states one (P4-02): an unconfirmed basis blocks approval until a person
    does, so a test that wants an approved, evaluable schedule models that explicit edit."""
    from app.domain.schedule.models import RecurrenceBasis

    schedule = schedule.model_copy(deep=True)
    for item in schedule.events:
        rule = item.recurrence
        if rule is not None and rule.basis_status == "UNCONFIRMED":
            rule.basis = rule.basis or RecurrenceBasis.FIRST_RESOLVED_OCCURRENCE
            rule.basis_status = "STATED"
    return schedule


def blocking_after_review(result):
    return sorted(issue.issue_code for issue in ScheduleValidator.blocking(
        ScheduleValidator().validate(stated(result.schedule))))


def approve(schedule):
    return schedule.model_copy(update={"schedule_metadata": schedule.schedule_metadata.model_copy(
        update={"status": ScheduleStatus.APPROVED})})


def statement(result, what):
    return [item for item in result.accountability["statements"] if item["what"] == what]


# --- weekday calendar series --------------------------------------------------------------

def rt_series(**policies):
    return fact("Radiotherapy", "Radiotherapy Monday to Friday, 10 fractions", fid="f1",
                timing={"pattern": "WEEKDAY_REPEAT", "from": "Start of radiotherapy", "weekdays": MON_FRI},
                repeat={"interval": "1 DAY", "end": {"type": "COUNT", "count": 10}}, **policies)


def rt_dates(schedule):
    ctx = context(schedule, anchors={"START_OF_RADIOTHERAPY": FRIDAY})
    items = by_code(evaluate(schedule, ctx, horizon=date(2027, 3, 31)))["RADIOTHERAPY"]
    return [item.timing.nominal_start if item.timing else None for item in items], items


def test_a_weekday_series_without_stated_policies_leaves_them_unstated_and_still_dates():
    result = compile_facts(anchor_fact("Start of radiotherapy", "a1"), rt_series(),
                           day_1="Start of radiotherapy")
    rule = result.schedule.events[0].calendar_recurrence
    assert rule is not None and result.schedule.events[0].recurrence is None
    assert [item.value for item in rule.weekdays] == ["MON", "TUE", "WED", "THU", "FRI"]
    assert rule.count_basis is None
    assert rule.holiday_policy.value == "NONE_STATED" and rule.missed_policy.value == "NONE_STATED"
    warnings = {issue.issue_code for issue in result.issues if not issue.blocking}
    assert {"CALENDAR_COUNT_BASIS_UNSTATED", "CALENDAR_HOLIDAY_POLICY_UNSTATED",
            "CALENDAR_MISSED_POLICY_UNSTATED"} <= warnings
    assert blocking(result) == []
    dates, _ = rt_dates(approve(result.schedule))
    assert dates == [date(2026, 10, day) for day in (2, 5, 6, 7, 8, 9, 12, 13, 14, 15)]
    assert statement(result, "Radiotherapy")[0]["disposition"] == L.COMPILED


def test_stated_policies_compile_and_a_site_calendar_makes_them_execute():
    holiday = fact("Radiotherapy", "Fractions falling on a public holiday are skipped", fid="f2", page=11,
                   timing={"pattern": "HOLIDAY_RULE", "from": "Start of radiotherapy"}, holiday_policy="SKIP")
    result = compile_facts(anchor_fact("Start of radiotherapy", "a1"),
                           rt_series(count_basis="SLOTS", missed_policy="NO_REPLACEMENT"), holiday,
                           day_1="Start of radiotherapy")
    event = result.schedule.events[0]
    rule = event.calendar_recurrence
    assert (rule.count_basis.value, rule.holiday_policy.value, rule.missed_policy.value) == (
        "SLOTS", "SKIP", "NO_REPLACEMENT")
    # A stated holiday policy needs the site's calendar; the compiler never names one.
    assert blocking(result) == ["CALENDAR_HOLIDAY_CALENDAR_REQUIRED"]
    assert {item["disposition"] for item in statement(result, "Radiotherapy")} == {L.COMPILED}

    site = HolidayCalendar(code="SITE_A", version="2026.1", business_weekdays=list(Weekday)[:5],
                           dates=[HOLIDAY])
    event.calendar_recurrence = rule.model_copy(update={"holiday_calendar_ref": "SITE_A"})
    supplied = result.schedule.model_copy(update={"holiday_calendars": [site]})
    assert not ScheduleValidator.blocking(ScheduleValidator().validate(supplied))
    dates, _ = rt_dates(approve(supplied))
    assert dates == [date(2026, 10, day) for day in (2, 5, 6, 7, 8, 9, 13, 14, 15, 16)]


def test_a_holiday_rule_without_a_stated_policy_stays_pending():
    holiday = fact("Radiotherapy", "Public holidays are handled per institutional practice", fid="f2",
                   timing={"pattern": "HOLIDAY_RULE", "from": "Start of radiotherapy"})
    result = compile_facts(anchor_fact("Start of radiotherapy", "a1"), rt_series(), holiday,
                           day_1="Start of radiotherapy")
    assert result.schedule.events[0].calendar_recurrence.holiday_policy.value == "NONE_STATED"
    assert "UNRESOLVED_STATEMENT" in blocking(result)
    pending = [item for item in statement(result, "Radiotherapy") if item["statement_id"] == "f2"]
    assert pending[0]["disposition"] == L.PENDING_INTERPRETATION


# --- processes -------------------------------------------------------------------------------

def treatment(drug_a="Drug A", drug_b="Drug B", follow="Survival follow-up", start="Treatment start",
              block="Treatment"):
    return [
        anchor_fact(start, "a1"),
        fact(f"{drug_a} dose", f"{drug_a} every 21 days for 6 cycles", fid="f1", block=block,
             component=drug_a, component_kind="INTERVENTION", event_type="TREATMENT",
             timing={"pattern": "REPEAT", "from": start},
             repeat={"interval": "21 DAY", "includes_start": True, "end": {"type": "COUNT", "count": 6}}),
        fact(f"{drug_b} dose", f"{drug_b} weekly for 12 weeks", fid="f2", block=block,
             component=drug_b, component_kind="INTERVENTION", event_type="TREATMENT",
             timing={"pattern": "REPEAT", "from": start},
             repeat={"interval": "7 DAY", "includes_start": True, "end": {"type": "COUNT", "count": 12}}),
        fact(f"{follow} contact", f"{follow} every 3 months", fid="f3", component=follow,
             component_kind="FOLLOW_UP", event_type="TELEPHONE_CONTACT",
             timing={"pattern": "REPEAT", "from": start},
             repeat={"interval": "3 MONTH", "includes_start": False, "end": {"type": "OPEN_ENDED"}}),
        fact("Grade 3 neuropathy", f"Grade 3 neuropathy: discontinue {drug_b}; continue {drug_a}", fid="c1",
             kind="condition", timing={"pattern": "DISCONTINUE"},
             condition={"name": "Neuropathy", "effect": "STOP", "target_components": [drug_b]}),
        fact("Progression", "Progression: discontinue all protocol treatment", fid="c2", kind="condition",
             timing={"pattern": "DISCONTINUE"},
             condition={"name": "Progression", "effect": "STOP", "targets": [block]}),
    ]


def compile_treatment(**names):
    start = names.get("start", "Treatment start")
    block = names.get("block", "Treatment")
    return compile_facts(*treatment(**names), day_1=start, blocks=[{"name": block, "dependency_mode": "NOMINAL",
                                                                     "evidence": [{"page": 10, "quote": "x"}]}])


def run(schedule, condition, start_code="TREATMENT_START"):
    conditions = {condition: PatientConditionStatus(condition_code=condition, state=ConditionState.ACTIVE,
                                                   occurrence_date=date(2026, 11, 20))}
    ctx = context(schedule, anchors={start_code: date(2026, 10, 5)}, conditions=conditions)
    return by_code(evaluate(schedule, ctx, horizon=date(2027, 12, 31)))


def cancelled(items):
    return sum(item.status.value == "CANCELLED" for item in items)


def test_components_become_processes_with_unstated_policies_left_unstated():
    result = compile_treatment()
    processes = {item.code: item for item in result.schedule.processes}
    assert set(processes) == {"DRUG_A", "DRUG_B", "SURVIVAL_FOLLOW_UP"}
    assert processes["DRUG_B"].event_codes == ["DRUG_B_DOSE"]
    assert processes["SURVIVAL_FOLLOW_UP"].kind.value == "FOLLOW_UP"
    assert all(item.hold_resume_policy.value == "NONE_STATED" and item.resume_trigger is None
               for item in processes.values())
    actions = {(item.code, action.action_type, action.target_code)
               for item in result.schedule.conditional_definitions for action in item.actions}
    assert ("NEUROPATHY", "STOP_PROCESS", "DRUG_B") in actions
    assert ("PROGRESSION", "STOP_BLOCK", "TREATMENT") in actions
    # the three repeats' bases are not stated by the protocol text: review is required (only that)
    assert set(blocking(result)) == {"RECURRENCE_BASIS_UNCONFIRMED"}
    assert blocking_after_review(result) == []


def test_a_per_drug_stop_cancels_only_that_drugs_obligations():
    schedule = approve(stated(compile_treatment().schedule))
    before = run(schedule, "UNRELATED")
    after = run(schedule, "NEUROPATHY")
    assert cancelled(after["DRUG_B_DOSE"]) > 0
    assert cancelled(after["DRUG_A_DOSE"]) == 0 and cancelled(after["SURVIVAL_FOLLOW_UP_CONTACT"]) == 0
    assert [item.model_dump() for item in after["DRUG_A_DOSE"]] == [
        item.model_dump() for item in before["DRUG_A_DOSE"]]


def test_a_whole_treatment_stop_cancels_every_drug_but_not_follow_up():
    schedule = approve(stated(compile_treatment().schedule))
    after = run(schedule, "PROGRESSION")
    assert cancelled(after["DRUG_A_DOSE"]) > 0 and cancelled(after["DRUG_B_DOSE"]) > 0
    assert cancelled(after["SURVIVAL_FOLLOW_UP_CONTACT"]) == 0


def test_stated_hold_policy_and_participation_rules_stay_distinct():
    facts = treatment()
    facts.append(fact("Grade 2 rash", "Hold Drug A for grade 2 rash; restart from the resume date", fid="c3",
                      kind="condition", timing={"pattern": "HOLD_RESUME"},
                      condition={"name": "Rash", "effect": "PAUSE", "target_components": ["Drug A"],
                                 "hold_resume_policy": "RESTART_FROM_RESUME"}))
    facts.append(fact("Treatment withdrawal", "Patients withdrawn from treatment continue follow-up", fid="c4",
                      kind="condition", timing={"pattern": "DISCONTINUE"},
                      condition={"name": "Treatment withdrawal", "effect": "STOP",
                                 "participation": "WITHDRAWAL_FROM_TREATMENT"}))
    facts.append(fact("Consent withdrawal", "Withdrawal of consent ends all study procedures", fid="c5",
                      kind="condition", timing={"pattern": "DISCONTINUE"},
                      condition={"name": "Consent withdrawal", "effect": "STOP",
                                 "participation": "WITHDRAWAL_OF_CONSENT"}))
    result = compile_facts(*facts, day_1="Treatment start",
                           blocks=[{"name": "Treatment", "dependency_mode": "NOMINAL"}])
    processes = {item.code: item for item in result.schedule.processes}
    assert processes["DRUG_A"].hold_resume_policy.value == "RESTART_FROM_RESUME"
    assert processes["DRUG_B"].hold_resume_policy.value == "NONE_STATED"
    actions = {(item.code, action.action_type, action.target_code)
               for item in result.schedule.conditional_definitions for action in item.actions}
    assert {("RASH", "HOLD_PROCESS", "DRUG_A"), ("RASH_RESUMED", "RESUME_PROCESS", "DRUG_A"),
            ("TREATMENT_WITHDRAWAL", "STOP_PROCESS", "DRUG_A"),
            ("TREATMENT_WITHDRAWAL", "STOP_PROCESS", "DRUG_B")} <= actions
    assert not any(target == "SURVIVAL_FOLLOW_UP" for _code, _action, target in actions)
    assert not any(code == "CONSENT_WITHDRAWAL" for code, _action, _target in actions)
    [rule] = [item for item in result.schedule.schedule_metadata.extensions
              if item.get("type") == "PARTICIPATION_RULE"]
    assert rule["participation"] == "WITHDRAWAL_OF_CONSENT"
    assert statement(result, "Consent withdrawal")[0]["disposition"] == L.COMPILED


def test_a_component_whose_kind_is_unstated_is_not_guessed():
    facts = treatment()
    facts[1] = facts[1].model_copy(update={"component_kind": None})
    result = compile_facts(*facts, day_1="Treatment start",
                           blocks=[{"name": "Treatment", "dependency_mode": "NOMINAL"}])
    assert "DRUG_A" not in {item.code for item in result.schedule.processes}
    assert "PROCESS_KIND_UNSTATED" in blocking(result)


# --- generalisation -------------------------------------------------------------------------

def test_renamed_components_compile_and_behave_identically():
    renamed = compile_treatment(drug_a="Agent X", drug_b="Agent Y", follow="Long-term monitoring",
                                start="Day zero", block="Induction")
    processes = {item.code: item.event_codes for item in renamed.schedule.processes}
    assert processes == {"AGENT_X": ["AGENT_X_DOSE"], "AGENT_Y": ["AGENT_Y_DOSE"],
                         "LONG_TERM_MONITORING": ["LONG_TERM_MONITORING_CONTACT"]}
    schedule = approve(stated(renamed.schedule))
    after = run(schedule, "NEUROPATHY", start_code="DAY_ZERO")
    original = run(approve(stated(compile_treatment().schedule)), "NEUROPATHY")
    assert [item.status.value for item in after["AGENT_Y_DOSE"]] == [
        item.status.value for item in original["DRUG_B_DOSE"]]
    assert cancelled(after["AGENT_X_DOSE"]) == 0


def test_processes_and_calendar_series_do_not_depend_on_batch_order():
    base = [acc.REG, acc.VISIT_2,
            acc.fact("Radiotherapy", 5, "Visit 2: 4 weeks after registration", component="Radiotherapy",
                     component_kind="INTERVENTION",
                     timing={"pattern": "WEEKDAY_REPEAT", "from": "Registration", "weekdays": MON_FRI},
                     repeat={"interval": "1 DAY", "end": {"type": "COUNT", "count": 10}}),
            acc.fact("CT scan", 9, "CT scan every 12 weeks from registration", component="Imaging",
                     component_kind="ASSESSMENT",
                     timing={"pattern": "REPEAT", "from": "Registration"},
                     repeat={"interval": "12 WEEK", "includes_start": False, "end": {"type": "OPEN_ENDED"}})]
    one = acc.extract(acc.Scripted(base))
    two = acc.extract(acc.Scripted(base, reverse=True), limits=ReaderLimits(pages_per_batch=1))
    assert acc.normalized(one) == acc.normalized(two)

    def processes(result):
        return sorted(json.dumps(item.model_dump(mode="json", exclude={"id", "evidence_refs"}), sort_keys=True)
                      for item in result.schedule.processes)
    assert processes(one) == processes(two) and len(processes(one)) == 2
    assert acc.event(one, "RADIOTHERAPY").calendar_recurrence is not None


def test_processes_and_calendar_series_survive_the_draft_import():
    from uuid import uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db.base import Base
    from app.db.repositories import ScheduleRepository
    from app.services.native_import import import_native_schedules

    result = compile_treatment()
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        _trial, [imported] = import_native_schedules(
            session, organization_id=uuid4(), actor_id=uuid4(), external_trial_id="t-wp3",
            protocol_number="WP3-1", study_title="WP3", document_uri="private://protocol-uploads/w.pdf",
            document_name="protocol.pdf", document_hash="w" * 64, definitions=[{
                "id": "sd-wp3", "native_schedule": result.schedule.model_dump(mode="json"),
                "native_issues": [item.model_dump(mode="json") for item in result.issues],
                "native_accountability": result.accountability}])
        session.commit()
        stored = ScheduleRepository(session).get(imported.schedule_version_id)
    assert {item.code for item in stored.processes} == {"DRUG_A", "DRUG_B", "SURVIVAL_FOLLOW_UP"}
