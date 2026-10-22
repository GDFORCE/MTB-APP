"""Alert routing and synthetic checks from ``deploy/monitoring`` (P13-02)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

import yaml

ROOT = Path(__file__).resolve().parents[3] / "deploy" / "monitoring"
METRIC_NAME = re.compile(r"\b(uctsm_[a-z0-9_]+)")
SELECTOR = re.compile(r"\b(uctsm_[a-z0-9_]+)\{([^}]*)\}")
SUFFIXES = ("_bucket", "_count", "_sum")


def load(name: str) -> dict:
    return yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))


def alerts() -> list[dict]:
    return [rule for group in load("alerts.yml")["groups"] for rule in group["rules"]]


def base_metric(name: str) -> str:
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def route(labels: dict[str, str], config: dict | None = None) -> dict:
    """The receiver chain (with escalation) for an alert's labels. First matching route wins."""
    config = config or load("routes.yml")
    for entry in config["routes"]:
        if all(labels.get(key) == value for key, value in entry["match"].items()):
            chain, name, seen = [], entry["receiver"], set()
            while name and name not in seen:
                seen.add(name)
                receiver = config["receivers"][name]
                chain.append({"receiver": name, "channel": receiver["channel"],
                              "escalate_after_minutes": receiver["escalate_after_minutes"]})
                name = receiver.get("then")
            return {"receiver": entry["receiver"], "chain": chain}
    raise LookupError("no route matched")


def run_synthetic_checks(fetch: Callable[[str], int], config: dict | None = None) -> list[dict]:
    """``fetch(path) -> status``. Returns one result per configured check."""
    results = []
    for check in (config or load("routes.yml"))["synthetic_checks"]:
        try:
            status = fetch(check["path"])
        except Exception:                                  # network error: the check fails, loudly
            status = 0
        results.append({"name": check["name"], "ok": status == check["expect_status"], "status": status})
    return results
