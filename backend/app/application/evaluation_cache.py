"""Bounded memo of pure calculations; never an authority or a persisted patient pointer."""
from collections import OrderedDict
from datetime import date
import threading

from app.domain.schedule.amendment import schedule_content_hash
from app.domain.schedule.evaluator import ScheduleEvaluator
from app.domain.schedule.incremental import EvaluationBaseline, supported


class EvaluationMemo:
    def __init__(self, *, max_entries=8, max_bytes=2 * 1024 * 1024):
        self.max_entries = max_entries; self.max_bytes = max_bytes
        self._entries = OrderedDict(); self._bytes = 0; self._lock = threading.RLock()

    def evaluate(self, organization_id, schedule, context, *, horizon, today=None, rolling=None, compiled=None):
        as_of = today or date.today()
        eligible = rolling is None and supported(schedule)
        key = (organization_id, context.patient_id, schedule.schedule_version_id, schedule_content_hash(schedule))
        with self._lock:
            cached = self._entries.get(key) if eligible else None
            baseline = cached[0] if cached else None
        result = ScheduleEvaluator().evaluate(schedule, context, horizon=horizon,
            today=as_of, rolling=rolling, compiled=compiled, baseline=baseline)
        if eligible:
            size = len(result.model_dump_json().encode()) + len(context.model_dump_json().encode())
            if size <= self.max_bytes and self.max_entries > 0:
                value = EvaluationBaseline(key[-1], context.model_copy(deep=True),
                    result.model_copy(deep=True), horizon, as_of)
                with self._lock:
                    prior = self._entries.pop(key, None)
                    if prior: self._bytes -= prior[1]
                    self._entries[key] = (value, size); self._bytes += size
                    while self._bytes > self.max_bytes or len(self._entries) > self.max_entries:
                        _, (_, removed) = self._entries.popitem(last=False); self._bytes -= removed
        return result


memo = EvaluationMemo()
