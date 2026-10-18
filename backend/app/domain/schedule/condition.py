from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import Field

from .timing import StrictModel


class TruthValue(StrEnum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"


class FieldOperand(StrictModel):
    source: Literal["FIELD"] = "FIELD"
    field: str = Field(min_length=1)


class LiteralOperand(StrictModel):
    source: Literal["LITERAL"] = "LITERAL"
    value: object


Operand = Annotated[Union[FieldOperand, LiteralOperand], Field(discriminator="source")]


class ComparisonCondition(StrictModel):
    type: Literal["COMPARISON"] = "COMPARISON"
    operator: Literal["EQUALS", "NOT_EQUALS", "GT", "GTE", "LT", "LTE"]
    left: Operand
    right: Operand


class MembershipCondition(StrictModel):
    type: Literal["MEMBERSHIP"] = "MEMBERSHIP"
    operator: Literal["IN", "NOT_IN"]
    value: Operand
    values: list[object]


class ExistsCondition(StrictModel):
    type: Literal["EXISTS"] = "EXISTS"
    field: str = Field(min_length=1)
    exists: bool = True


class UnsupportedCondition(StrictModel):
    """A source predicate the condition language cannot express (capability gap).

    It evaluates to UNKNOWN - never FALSE - so missing capability can never
    become a negative applicability decision (RL-06), and the validator's
    capability check blocks approval of any rule containing it (RL-12).
    """

    type: Literal["UNSUPPORTED"] = "UNSUPPORTED"
    source_operator: str = Field(min_length=1)
    source_text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, object] = Field(default_factory=dict)


class AllCondition(StrictModel):
    type: Literal["AND"] = "AND"
    conditions: list["ConditionExpression"] = Field(min_length=1)


class AnyCondition(StrictModel):
    type: Literal["OR"] = "OR"
    conditions: list["ConditionExpression"] = Field(min_length=1)


class NotCondition(StrictModel):
    type: Literal["NOT"] = "NOT"
    condition: "ConditionExpression"


ConditionExpression = Annotated[
    Union[
        ComparisonCondition,
        MembershipCondition,
        ExistsCondition,
        AllCondition,
        AnyCondition,
        NotCondition,
        UnsupportedCondition,
    ],
    Field(discriminator="type"),
]


AllCondition.model_rebuild()
AnyCondition.model_rebuild()
NotCondition.model_rebuild()


def _field_value(context: dict[str, object], path: str) -> tuple[bool, object]:
    parts = path.split(".")
    if parts and parts[0] in {"patient", "context"}:
        parts = parts[1:]
    value: object = context
    for part in parts:
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _operand_value(operand: Operand, context: dict[str, object]) -> tuple[bool, object]:
    if isinstance(operand, LiteralOperand):
        return True, operand.value
    return _field_value(context, operand.field)


def unresolved_inputs(expression: ConditionExpression, context: dict[str, object]) -> list[str]:
    """Why a condition is not decided: the field paths the patient context does not
    hold and any predicate the condition language cannot express. Informational;
    ``evaluate_condition`` stays the only truth value."""
    missing: list[str] = []

    def walk(node: ConditionExpression) -> None:
        if isinstance(node, (AllCondition, AnyCondition)):
            for child in node.conditions:
                walk(child)
        elif isinstance(node, NotCondition):
            walk(node.condition)
        elif isinstance(node, UnsupportedCondition):
            missing.append(f"unsupported predicate: {node.source_operator}")
        elif isinstance(node, ExistsCondition):
            return
        elif isinstance(node, MembershipCondition):
            if isinstance(node.value, FieldOperand) and not _field_value(context, node.value.field)[0]:
                missing.append(node.value.field)
        elif isinstance(node, ComparisonCondition):
            for operand in (node.left, node.right):
                if isinstance(operand, FieldOperand) and not _field_value(context, operand.field)[0]:
                    missing.append(operand.field)

    walk(expression)
    return list(dict.fromkeys(missing))


def evaluate_condition(expression: ConditionExpression, context: dict[str, object]) -> TruthValue:
    if isinstance(expression, AllCondition):
        values = [evaluate_condition(item, context) for item in expression.conditions]
        if TruthValue.FALSE in values:
            return TruthValue.FALSE
        return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.TRUE
    if isinstance(expression, AnyCondition):
        values = [evaluate_condition(item, context) for item in expression.conditions]
        if TruthValue.TRUE in values:
            return TruthValue.TRUE
        return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.FALSE
    if isinstance(expression, NotCondition):
        result = evaluate_condition(expression.condition, context)
        return {
            TruthValue.TRUE: TruthValue.FALSE,
            TruthValue.FALSE: TruthValue.TRUE,
            TruthValue.UNKNOWN: TruthValue.UNKNOWN,
        }[result]
    if isinstance(expression, UnsupportedCondition):
        return TruthValue.UNKNOWN
    if isinstance(expression, ExistsCondition):
        found, value = _field_value(context, expression.field)
        result = found and value is not None
        return TruthValue.TRUE if result == expression.exists else TruthValue.FALSE
    if isinstance(expression, MembershipCondition):
        found, value = _operand_value(expression.value, context)
        if not found:
            return TruthValue.UNKNOWN
        result = value in expression.values
        if expression.operator == "NOT_IN":
            result = not result
        return TruthValue.TRUE if result else TruthValue.FALSE

    left_found, left = _operand_value(expression.left, context)
    right_found, right = _operand_value(expression.right, context)
    if not left_found or not right_found:
        return TruthValue.UNKNOWN
    try:
        result = {
            "EQUALS": lambda: left == right,
            "NOT_EQUALS": lambda: left != right,
            "GT": lambda: left > right,
            "GTE": lambda: left >= right,
            "LT": lambda: left < right,
            "LTE": lambda: left <= right,
        }[expression.operator]()
    except (TypeError, ValueError):
        return TruthValue.UNKNOWN
    return TruthValue.TRUE if result else TruthValue.FALSE

