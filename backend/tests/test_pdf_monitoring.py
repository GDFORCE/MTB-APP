"""P13-02: monitoring-as-code stays consistent with the real metrics, runbooks and routing."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability import routing
from app.observability.install import build_router
from app.observability.metrics import METRICS

REPO = Path(__file__).resolve().parents[2]


def test_every_alert_names_real_metrics_and_labels_and_has_owner_severity_and_runbook():
    rules = routing.alerts()
    assert len(rules) >= 7 and len({rule["alert"] for rule in rules}) == len(rules)
    for rule in rules:
        assert rule["labels"]["owner"] and rule["labels"]["severity"] in {"page", "ticket"}, rule["alert"]
        runbook = REPO / rule["annotations"]["runbook"]
        assert runbook.is_file() and runbook.read_text(encoding="utf-8").strip(), rule["alert"]
        for name in routing.METRIC_NAME.findall(rule["expr"]):
            assert routing.base_metric(name) in METRICS._metrics, (rule["alert"], name)
        for name, selector in routing.SELECTOR.findall(rule["expr"]):
            known = set(METRICS._metrics[routing.base_metric(name)].labels) | {"le"}
            used = {part.split("=")[0].split("!")[0].strip() for part in selector.split(",") if part.strip()}
            assert used <= known, (rule["alert"], used - known)


def test_alerts_cover_correctness_latency_queues_migrations_and_legacy_writes():
    names = {rule["alert"] for rule in routing.alerts()}
    assert {"UctsmWorkflowErrors", "UctsmEvaluateLatencyHigh", "UctsmOutboxDeadLetters",
            "UctsmOutboxBacklog", "UctsmLegacyWriteAfterCutover", "UctsmJobFailures"} <= names


@pytest.mark.parametrize("labels, receiver, channel", [
    ({"owner": "platform-oncall", "severity": "page"}, "platform-pager", "pager"),
    ({"owner": "platform-oncall", "severity": "ticket"}, "platform-ticket", "ticket-queue"),
    ({"owner": "migration-lead", "severity": "page"}, "migration-pager", "pager"),
    ({"owner": "someone-new", "severity": "weird"}, "platform-pager", "pager"),      # never dropped
])
def test_alert_routing_reaches_the_right_receiver(labels, receiver, channel):
    result = routing.route(labels)
    assert result["receiver"] == receiver and result["chain"][0]["channel"] == channel


def test_every_declared_alert_routes_and_escalation_terminates():
    for rule in routing.alerts():
        chain = routing.route(rule["labels"])["chain"]
        assert chain[-1]["receiver"] == "engineering-manager"
        assert len({item["receiver"] for item in chain}) == len(chain)


def test_synthetic_checks_pass_and_a_failing_or_unreachable_target_is_reported():
    app = FastAPI(); state = {"ok": True}
    app.include_router(build_router(lambda: {"mongo": state["ok"]}))
    client = TestClient(app)
    assert all(item["ok"] for item in routing.run_synthetic_checks(lambda path: client.get(path).status_code))
    state["ok"] = False
    failed = routing.run_synthetic_checks(lambda path: client.get(path).status_code)
    assert [item["name"] for item in failed if not item["ok"]] == ["ready"]

    def unreachable(path):
        raise ConnectionError("down")
    assert not any(item["ok"] for item in routing.run_synthetic_checks(unreachable))
