"""Compare two quality reports (a baseline and a current run) and name every regression (P8-03/05 trend)."""
from __future__ import annotations

from typing import Any

SCHEMA = "uctsm-quality-comparison.v1"
RATES = ("precision", "recall")


def _rates(report: dict[str, Any]) -> dict[str, float | None]:
    rows: dict[str, float | None] = {}
    for record, metric in report.get("metrics", {}).items():
        for name in RATES:
            rows[f"fields:{record}:{name}"] = metric.get(name)
    for record, parts in report.get("components", {}).items():
        for part, metric in parts.items():
            if part == "identity":
                rows[f"identity:{record}:match"] = 1.0 if metric.get("match") else 0.0
            else:
                for name in RATES:
                    rows[f"{part}:{record}:{name}"] = metric.get(name)
    return rows


def compare_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Regressions are anything that got worse or newly appeared; a missing or unmeasurable value is a regression
    when the baseline had a number (never silently treated as unchanged)."""
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    if baseline.get("decision") == "PASS" and current.get("decision") != "PASS":
        regressions.append({"kind": "DECISION", "from": baseline.get("decision"), "to": current.get("decision")})
    for code in sorted(set(current.get("reason_codes", [])) - set(baseline.get("reason_codes", []))):
        regressions.append({"kind": "NEW_REASON", "code": code})
    for code in sorted(set(baseline.get("reason_codes", [])) - set(current.get("reason_codes", []))):
        improvements.append({"kind": "RESOLVED_REASON", "code": code})
    before, after = _rates(baseline), _rates(current)
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is not None and new is None:
            regressions.append({"kind": "METRIC_LOST", "metric": key, "from": old})
        elif old is not None and new is not None and new < old:
            regressions.append({"kind": "METRIC_DROP", "metric": key, "from": old, "to": new})
        elif old is not None and new is not None and new > old:
            improvements.append({"kind": "METRIC_GAIN", "metric": key, "from": old, "to": new})
    if current.get("unsafe_dates", 0) > baseline.get("unsafe_dates", 0):
        regressions.append({"kind": "UNSAFE_DATES", "from": baseline.get("unsafe_dates", 0), "to": current["unsafe_dates"]})
    for record, metric in current.get("metrics", {}).items():
        was = baseline.get("metrics", {}).get(record, {}).get("critical_false_positives", 0)
        if metric.get("critical_false_positives", 0) > was:
            regressions.append({"kind": "CRITICAL_FALSE_POSITIVE", "record": record, "from": was,
                                "to": metric["critical_false_positives"]})
    return {"schema": SCHEMA, "regressed": bool(regressions), "regressions": regressions, "improvements": improvements,
            "baseline": {"dataset_hash": baseline.get("dataset_hash"), "config_hash": baseline.get("config_hash"),
                         "versions": baseline.get("versions")},
            "current": {"dataset_hash": current.get("dataset_hash"), "config_hash": current.get("config_hash"),
                        "versions": current.get("versions")},
            "dataset_changed": baseline.get("dataset_hash") != current.get("dataset_hash")}
