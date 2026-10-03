from datetime import date

from schedule import build, day_to_date

D1 = date(2026, 10, 1)


def test_day_math_without_day_zero():
    assert day_to_date(D1, 1, False) == D1
    assert day_to_date(D1, 8, False) == date(2026, 10, 8)
    assert day_to_date(D1, -1, False) == date(2026, 9, 30)
    assert day_to_date(D1, -28, False) == date(2026, 9, 3)


def test_day_math_with_day_zero():
    assert day_to_date(D1, 0, True) == date(2026, 9, 30)
    assert day_to_date(D1, -1, True) == date(2026, 9, 29)


def test_build_windows_repeats_and_event_driven():
    plan = {
        "has_day_zero": False,
        "visits": [
            {"name": "End of treatment", "day": None, "activities": []},
            {"name": "Screening", "day": -28, "window_before": 0, "window_after": 0, "activities": ["Consent"]},
            {"name": "Baseline", "day": 1, "window_before": None, "window_after": None, "activities": []},
        ],
        "repeating": [
            {"name_template": "Cycle {n} Day 1", "number_start": 2, "first_day": 22, "every_days": 21,
             "occurrences": 3, "until_text": None, "window_before": 3, "window_after": 3, "activities": []},
            {"name_template": "Follow-up {n}", "number_start": 1, "first_day": 200, "every_days": 84,
             "occurrences": None, "until_text": "until death", "window_before": 7, "window_after": 7,
             "activities": []},
        ],
    }
    rows = build(plan, D1, open_ended_repeats=2)
    names = [r.visit for r in rows]
    assert names == ["Screening", "Baseline", "Cycle 2 Day 1", "Cycle 3 Day 1", "Cycle 4 Day 1",
                     "Follow-up 1", "Follow-up 2", "End of treatment"]
    c2 = rows[2]
    assert c2.target == date(2026, 10, 22)
    assert (c2.earliest, c2.latest) == (date(2026, 10, 19), date(2026, 10, 25))
    assert rows[-1].target is None
    assert "first 2 shown" in rows[5].note
