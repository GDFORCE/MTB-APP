"""Writes the generated activities and confinements of one evaluation.

Extracted from ``PatientScheduleService`` (P12-02)."""
from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select

from app.db import models as db
from app.domain.schedule.identity import activity_logical_key


from app.services.patient_schedule_context import PatientScheduleContext


class PatientSchedulePersistence(PatientScheduleContext):
    def _persist_activities(
        self, row: db.PatientEvent, item: object,
        *, schedule_version_id: UUID | None = None, protocol_version_id: UUID | None = None,
    ) -> None:
        """Materialize the day-wise activity schedule for one visit occurrence."""
        activities = getattr(item, "activities", None) or []
        if not activities:
            return
        self.session.flush()
        event_code = getattr(item, "event_code", None) or row.logical_key
        occurrence_index = getattr(item, "occurrence_index", row.occurrence_index)

        def as_datetime(value: date | datetime | None) -> datetime | None:
            if value is None or isinstance(value, datetime):
                return value
            return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)

        for activity in activities:
            timing = activity.timing
            self.session.add(db.PatientActivity(
                patient_event_id=row.id,
                activity_definition_id=activity.activity_definition_id,
                activity_code=activity.activity_code,
                logical_key=activity_logical_key(
                    event_code, occurrence_index,
                    activity.activity_code or str(activity.activity_definition_id),
                ),
                status=activity.status.value,
                requiredness=activity.requiredness.value,
                sequence_number=activity.sequence_number,
                planned_time=as_datetime(timing.nominal_start) if timing else None,
                earliest_time=as_datetime(timing.earliest) if timing else None,
                latest_time=as_datetime(timing.latest) if timing else None,
                actual_time=as_datetime(activity.actual_time),
                timing_resolution=timing.model_dump(mode="json") if timing else {},
                generation_reason=activity.explanation,
                schedule_version_id=schedule_version_id,
                protocol_version_id=protocol_version_id,
            ))

    def _persist_confinements(
        self,
        patient_schedule_id: UUID,
        schedule_evaluation_id: UUID,
        confinements: list,
        *,
        prior_evaluation_id: UUID | None,
        schedule_version_id: UUID,
        protocol_version_id: UUID | None,
    ) -> list["db.PatientConfinementEpisode"]:
        """Materialize this evaluation's confinement episodes (AD-8).

        Before this, ``result.confinements`` was computed on every evaluation
        and then discarded - every consumer (the operational bridge, the
        deviation report, reminder delivery) recomputed it fresh from a
        transient ``EvaluationResult`` instead of reading a persisted row, the
        same way ``PatientEvent`` already is.

        Mirrors ``evaluate()``'s own event reconciliation: a DISCHARGED
        episode's actual dates are protected across re-evaluation once
        recorded, found via ``episode_code`` as the stable logical key
        (Step 1's identity discipline - an episode is not per-occurrence
        indexed the way an event is, so there is nothing else to key on).
        """
        def as_date(value: date | datetime | None) -> date | None:
            return value.date() if isinstance(value, datetime) else value

        prior_by_key: dict[str, db.PatientConfinementEpisode] = {}
        if prior_evaluation_id is not None:
            prior_by_key = {
                item.logical_key: item
                for item in self.session.scalars(select(db.PatientConfinementEpisode).where(
                    db.PatientConfinementEpisode.schedule_evaluation_id == prior_evaluation_id,
                ))
            }
        persisted: list[db.PatientConfinementEpisode] = []
        for item in confinements:
            logical_key = item.episode_code
            previous = prior_by_key.get(logical_key)
            protected = previous is not None and previous.status == "DISCHARGED"
            row = db.PatientConfinementEpisode(
                patient_schedule_id=patient_schedule_id, schedule_evaluation_id=schedule_evaluation_id,
                episode_definition_id=item.episode_definition_id, episode_code=item.episode_code,
                logical_key=logical_key,
                supersedes_patient_confinement_episode_id=previous.id if previous else None,
                protected_history=protected,
                status=(previous.status if protected and previous else item.status.value),
                planned_admission=(previous.planned_admission if protected and previous else as_date(item.planned_admission)),
                actual_admission=(previous.actual_admission if protected and previous else as_date(item.actual_admission)),
                planned_discharge=(previous.planned_discharge if protected and previous else as_date(item.planned_discharge)),
                actual_discharge=(previous.actual_discharge if protected and previous else as_date(item.actual_discharge)),
                extended_by=(previous.extended_by if protected and previous else list(item.extended_by)),
                explanation=(previous.explanation if protected and previous else dict(item.explanation)),
                schedule_version_id=schedule_version_id, protocol_version_id=protocol_version_id,
            )
            self.session.add(row)
            persisted.append(row)
        return persisted
