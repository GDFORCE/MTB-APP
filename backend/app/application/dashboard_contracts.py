"""Dashboard DTOs and wording independent of SQL, Mongo and provider clients."""
from datetime import date
from enum import StrEnum
from pydantic import BaseModel, Field

UPCOMING_WINDOW_DAYS = 7

class ActionCategory(StrEnum):
    ANCHOR_CONFIRMATION = "ANCHOR_CONFIRMATION"
    CONDITION_CONFIRMATION = "CONDITION_CONFIRMATION"
    CONDITION_ACTIVE = "CONDITION_ACTIVE"
    IMPACT_CONFIRMATION = "IMPACT_CONFIRMATION"
    VISIT_DUE = "VISIT_DUE"
    VISIT_OVERDUE = "VISIT_OVERDUE"
    DEVIATION = "DEVIATION"
    ACTIVITY_INCOMPLETE = "ACTIVITY_INCOMPLETE"

PRIORITY = {
    ActionCategory.IMPACT_CONFIRMATION: 0,
    ActionCategory.ANCHOR_CONFIRMATION: 1,
    ActionCategory.CONDITION_CONFIRMATION: 2,
    ActionCategory.VISIT_OVERDUE: 3,
    ActionCategory.DEVIATION: 4,
    ActionCategory.VISIT_DUE: 5,
    ActionCategory.ACTIVITY_INCOMPLETE: 6,
    ActionCategory.CONDITION_ACTIVE: 7,
}

class DashboardAction(BaseModel):
    patient_id: str
    patient_code: str
    category: ActionCategory
    message: str
    due_date: date | None = None
    #: What the UI should open when the item is selected.
    target_type: str
    target_id: str | None = None
    priority: int = 5

class DashboardResult(BaseModel):
    trial_id: str | None = None
    generated_on: date
    actions: list[DashboardAction] = Field(default_factory=list)

    def by_category(self, category: ActionCategory) -> list[DashboardAction]:
        return [item for item in self.actions if item.category == category]

def _humanize(code: str) -> str:
    return code.replace("_", " ").title()

def _visit_mode(event_type: str) -> str:
    # Doc s13: the same vocabulary the extractor/adapter/validator write
    # (CORE_EVENT_TYPES), not a third list of near-miss strings. This used to
    # key on PHONE_CONTACT/REMOTE_VISIT/LAB_ONLY/IMAGING_ONLY/PROCEDURE_ONLY/
    # INPATIENT_CONFINEMENT, none of which a real event ever carries, so every
    # dashboard row silently fell back to the generic humanised label below.
    labels = {
        "SITE_VISIT": "site visit", "TELEPHONE_CONTACT": "telephone",
        "REMOTE": "remote visit", "HOME_VISIT": "home visit",
        "LABORATORY": "lab only", "IMAGING": "imaging",
        "PROCEDURE": "procedure", "INPATIENT_ADMISSION": "admission",
        "INPATIENT_DISCHARGE": "discharge",
    }
    return labels.get(event_type.upper(), event_type.replace("_", " ").lower())
