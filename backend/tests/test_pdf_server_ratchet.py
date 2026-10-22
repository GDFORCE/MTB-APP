"""P12-05: no new scheduling business logic may be added to server.py."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import server_ratchet as ratchet  # noqa: E402


def test_server_py_has_not_grown_past_its_baseline():
    assert ratchet.problems(ratchet.measure(), json.loads(ratchet.BASELINE.read_text(encoding="utf-8"))) == []


def test_growth_a_new_function_or_a_new_scheduling_function_is_each_reported():
    base = {"lines": 100, "functions": 10, "scheduling_functions": ["list_visits"]}
    assert ratchet.problems(base, base) == []
    assert ratchet.problems({**base, "lines": 101}, base)
    assert ratchet.problems({**base, "functions": 11}, base)
    added = ratchet.problems({**base, "scheduling_functions": ["list_visits", "compute_new_visit_dates"]}, base)
    assert added == ["new scheduling-related functions in server.py: compute_new_visit_dates"]
    assert ratchet.problems({"lines": 90, "functions": 9, "scheduling_functions": []}, base) == []   # shrinking is fine


def test_update_can_only_lower_the_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(ratchet, "BASELINE", tmp_path / "baseline.json")
    monkeypatch.setattr(ratchet, "measure", lambda: {"lines": 120, "functions": 10, "scheduling_functions": []})
    (tmp_path / "baseline.json").write_text(json.dumps({"lines": 100, "functions": 10, "scheduling_functions": []}))
    monkeypatch.setattr(sys, "argv", ["x", "--update"])
    assert ratchet.main() == 1                                         # refuses to raise it
    assert json.loads((tmp_path / "baseline.json").read_text())["lines"] == 100
    monkeypatch.setattr(ratchet, "measure", lambda: {"lines": 80, "functions": 9, "scheduling_functions": []})
    assert ratchet.main() == 0 and json.loads((tmp_path / "baseline.json").read_text())["lines"] == 80
