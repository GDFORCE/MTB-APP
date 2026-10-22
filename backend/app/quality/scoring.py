"""Exact typed field scoring with explicit denominators and critical false positives."""
from collections import Counter

from .models import ExpectedField


def _same(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _equal(expected: ExpectedField, actual: ExpectedField) -> bool:
    if expected.equivalence == "UNORDERED_STRINGS":
        left, right = expected.value, actual.value
        if not (isinstance(left, list) and isinstance(right, list)
                and all(isinstance(row, str) for row in [*left, *right])):
            return False
        return Counter(left) == Counter(right)
    # Python considers True == 1: that is unsafe for typed protocol fields.
    return _same(expected.value, actual.value)


def score_fields(expected: list[ExpectedField], predicted: list[ExpectedField]) -> dict:
    truth = {(row.rule_id, row.path): row for row in expected}
    if len(truth) != len(expected):
        raise ValueError("gold field keys must be unique")
    seen = set(); first = {}; tp = fp = critical_fp = 0; failures = []
    families = {}
    for row in predicted:
        key = (row.rule_id, row.path); target = truth.get(key)
        first.setdefault(key, row)
        family = row.path.split("/", 1)[0]
        metric = families.setdefault(family, {"tp": 0, "fp": 0, "fn": 0})
        if key not in seen and target is not None and _equal(target, row):
            tp += 1; metric["tp"] += 1
        else:
            fp += 1; metric["fp"] += 1
            # A predictor cannot downgrade severity supplied by the gold oracle.
            critical = target.critical if target is not None else True
            critical_fp += int(critical)
            failures.append({"rule_id": row.rule_id, "path": row.path,
                             "category": "DUPLICATE" if key in seen else
                             "EXTRA_EXECUTABLE_FIELD" if target is None else "FIELD_MISMATCH",
                             "critical": critical})
        seen.add(key)
    for key, target in truth.items():
        match = first.get(key)
        if match is None or not _equal(target, match):
            families.setdefault(target.path.split("/", 1)[0], {"tp": 0, "fp": 0, "fn": 0})["fn"] += 1
            failures.append({"rule_id": target.rule_id, "path": target.path,
                             "category": "MISSING_OR_WRONG_FIELD", "critical": target.critical})
    fn = len(truth) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if (
        precision is not None and recall is not None and precision + recall) else (
        0.0 if precision == 0 or recall == 0 else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "critical_false_positives": critical_fp,
            "families": families, "failures": failures}
