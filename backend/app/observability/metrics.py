"""Dependency-free metrics registry with Prometheus text exposition (P13-01/P13-02).

Labels are a closed set per metric and values are length/charset limited, so a patient id or
free text cannot become a label. The registry is process-local; scrape each worker.
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import contextmanager
from typing import Any

_LABEL_VALUE = re.compile(r"[^A-Za-z0-9_./:{}-]")
BUCKETS = (0.005, 0.025, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
MAX_SERIES = 500


def _clean(value) -> str:
    return _LABEL_VALUE.sub("_", str(value))[:80]


class _Metric:
    kind = ""

    def __init__(self, name: str, help_text: str, labels: tuple[str, ...]):
        self.name, self.help, self.labels = name, help_text, labels
        self._lock = threading.Lock()
        self._series: dict[tuple, Any] = {}

    def _key(self, labels: dict) -> tuple:
        if set(labels) != set(self.labels):
            raise ValueError(f"{self.name} takes labels {self.labels}")
        return tuple(_clean(labels[name]) for name in self.labels)

    def _slot(self, key: tuple, factory):
        if key not in self._series:
            if len(self._series) >= MAX_SERIES:
                key = tuple("overflow" for _ in self.labels)      # bounded cardinality
            self._series.setdefault(key, factory())
        return key

    def render(self) -> list[str]:
        raise NotImplementedError

    def _fmt(self, key: tuple, extra: str = "") -> str:
        pairs = [f'{name}="{value}"' for name, value in zip(self.labels, key)]
        return "{" + ",".join(pairs + ([extra] if extra else [])) + "}" if pairs or extra else ""


class Counter(_Metric):
    kind = "counter"

    def inc(self, amount: float = 1, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            key = self._slot(key, lambda: 0.0)
            self._series[key] += amount

    def value(self, **labels) -> float:
        return self._series.get(self._key(labels), 0.0)

    def render(self) -> list[str]:
        with self._lock:
            return [f"{self.name}{self._fmt(key)} {value:g}" for key, value in sorted(self._series.items())]


class Gauge(Counter):
    kind = "gauge"

    def set(self, value: float, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            key = self._slot(key, lambda: 0.0)
            self._series[key] = float(value)


class Histogram(_Metric):
    kind = "histogram"

    def observe(self, seconds: float, **labels) -> None:
        key = self._key(labels)
        with self._lock:
            key = self._slot(key, lambda: [[0] * len(BUCKETS), 0, 0.0])
            counts, total, accumulated = self._series[key]
            for index, bound in enumerate(BUCKETS):
                if seconds <= bound:
                    counts[index] += 1
            self._series[key] = [counts, total + 1, accumulated + seconds]

    def count(self, **labels) -> int:
        row = self._series.get(self._key(labels))
        return row[1] if row else 0

    def render(self) -> list[str]:
        lines = []
        with self._lock:
            for key, (counts, total, accumulated) in sorted(self._series.items()):
                for bound, count in zip(BUCKETS, counts):
                    le = 'le="%g"' % bound
                    lines.append(f"{self.name}_bucket{self._fmt(key, le)} {count}")
                lines.append(f"{self.name}_bucket{self._fmt(key, 'le=' + chr(34) + '+Inf' + chr(34))} {total}")
                lines.append(f"{self.name}_sum{self._fmt(key)} {accumulated:.6f}")
                lines.append(f"{self.name}_count{self._fmt(key)} {total}")
        return lines


class Registry:
    def __init__(self):
        self._metrics: dict[str, _Metric] = {}
        self.requests: Counter = self.counter("uctsm_http_requests_total", "HTTP requests", ("method", "route", "status"))
        self.evaluations: Histogram = self.histogram(
            "uctsm_workflow_seconds", "Workflow latency by stage and outcome", ("stage", "outcome"))
        self.outbox = self.counter("uctsm_outbox_results_total", "Outbox delivery results", ("result",))
        self.legacy_blocked = self.counter(
            "uctsm_legacy_write_blocked_total", "Legacy schedule writes refused after cutover", ("route",))
        self.legacy_consumers: Counter = self.counter(
            "uctsm_legacy_consumer_requests_total", "Requests to legacy scheduling routes slated for migration",
            ("route", "method", "classification"))
        self.restore_drill_status: Gauge = self.gauge(
            "uctsm_restore_drill_status", "1 when the last restore drill passed, 0 when it failed")
        self.restore_drill_last_success: Gauge = self.gauge(
            "uctsm_restore_drill_last_success_timestamp_seconds", "Unix time of the last passing restore drill")
        self.manual_decisions: Counter = self.counter(
            "uctsm_manual_rule_decisions_total", "Manual-only / unsupported rule decisions", ("action",))
        self.job_failures = self.counter("uctsm_job_failures_total", "Background job failures", ("job", "code"))
        self.queue_depth = self.gauge("uctsm_queue_depth", "Messages waiting by queue and state", ("queue", "state"))

    def _register(self, metric: _Metric):
        if metric.name in self._metrics:
            raise ValueError(f"duplicate metric {metric.name}")
        self._metrics[metric.name] = metric
        return metric

    def counter(self, name, help_text, labels=()) -> Counter:
        return self._register(Counter(name, help_text, tuple(labels)))  # type: ignore[return-value]

    def gauge(self, name, help_text, labels=()) -> Gauge:
        return self._register(Gauge(name, help_text, tuple(labels)))  # type: ignore[return-value]

    def histogram(self, name, help_text, labels=()) -> Histogram:
        return self._register(Histogram(name, help_text, tuple(labels)))  # type: ignore[return-value]

    @contextmanager
    def timed(self, stage: str):
        started = time.perf_counter()
        outcome = "ok"
        try:
            yield
        except Exception:
            outcome = "error"
            raise
        finally:
            self.evaluations.observe(time.perf_counter() - started, stage=stage, outcome=outcome)

    def render(self) -> str:
        out = []
        for metric in self._metrics.values():
            out.append(f"# HELP {metric.name} {metric.help}")
            out.append(f"# TYPE {metric.name} {metric.kind}")
            out.extend(metric.render())
        return "\n".join(out) + "\n"


METRICS = Registry()
