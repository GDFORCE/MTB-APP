"""Small HTTP identity/error adapters shared by the SQL and Mongo routers."""
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.authorization.maker_checker import MakerCheckerViolation


def _uuid(value: object, label: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise HTTPException(403, f"Authenticated user has no valid {label}") from error


def _identity(user: dict[str, Any]) -> tuple[UUID, UUID]:
    actor = _uuid(user.get("id"), "identity")
    organization = user.get("organization_id") or user.get("org_id")
    return actor, _uuid(organization, "organization context")


def _maker_checker_http(error: MakerCheckerViolation) -> HTTPException:
    outcome = error.outcome
    return HTTPException(403, str(error), headers={
        "X-Authz-Code": "MAKER_CHECKER", "X-Authz-Reason": outcome.reason_code,
        "X-Authz-Policy": outcome.policy_version})
