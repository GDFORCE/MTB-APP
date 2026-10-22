"""Scoring of the non-field gold components: identity decision, evidence citations, amendment mappings (P8-01/03)."""
from __future__ import annotations

import re

from .models import ACCEPTING, NOT_ACCEPTABLE, ExpectedEvidence, ExpectedIdentity, ExpectedMapping

COMPONENTS = ("fields", "identity", "evidence", "mappings", "scenarios")


def _quote(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def evidence_key(row: ExpectedEvidence) -> tuple:
    return (row.rule_id, row.page, _quote(row.quote))


def mapping_key(row: ExpectedMapping) -> tuple:
    return (tuple(sorted(row.source_codes)), tuple(sorted(row.target_codes)), row.mapping_type, row.requires_review)


def _prf(expected: set, predicted: list) -> dict:
    seen, tp, fp, failures = set(), 0, 0, []
    for key in predicted:
        if key in expected and key not in seen:
            tp += 1
        else:
            fp += 1
            failures.append({"category": "DUPLICATE" if key in seen else "EXTRA", "key": repr(key)})
        seen.add(key)
    for key in sorted(expected - seen, key=repr):
        failures.append({"category": "MISSING", "key": repr(key)})
    fn = len(expected) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "failures": failures}


def score_evidence(expected: list[ExpectedEvidence], predicted: list[ExpectedEvidence]) -> dict:
    return _prf({evidence_key(row) for row in expected}, [evidence_key(row) for row in predicted])


def score_mappings(expected: list[ExpectedMapping], predicted: list[ExpectedMapping]) -> dict:
    return _prf({mapping_key(row) for row in expected}, [mapping_key(row) for row in predicted])


def score_identity(expected: ExpectedIdentity, predicted: ExpectedIdentity | None) -> dict:
    if predicted is None:
        return {"match": False, "expected": expected.status, "actual": None, "critical_false_accept": False,
                "missing": True}
    return {"match": predicted.status == expected.status, "expected": expected.status, "actual": predicted.status,
            "critical_false_accept": expected.status in NOT_ACCEPTABLE and predicted.status in ACCEPTING,
            "missing": False}
