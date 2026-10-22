"""Reuse validation only for a verified immutable version and exact engine versions."""
from app.domain.schedule.compiled import artifact_key, compile_schedule
from app.domain.schedule.evaluator import ScheduleEvaluator

from .amendment_common import load_version


def compiled_for(tx, schedule, organization_id):
    version = load_version(tx, organization_id, schedule.schedule_version_id)
    # Historical hashless versions retain full validation and are never promoted
    # into verified content merely because they were read or evaluated.
    if version.header.content_hash is None:
        return None
    key = artifact_key(schedule, ScheduleEvaluator.version)
    cached = tx.schedules.get_compiled(schedule.schedule_version_id, key)
    if cached is not None:
        cached.verify_for(schedule, ScheduleEvaluator.version)
        return cached
    # A read-only amendment simulation also calls this function. A cache miss
    # must not turn a read into a write. New approvals persist the artifact in
    # their guarded transaction; older compatible content is validated locally.
    return compile_schedule(schedule, evaluator_version=ScheduleEvaluator.version)
