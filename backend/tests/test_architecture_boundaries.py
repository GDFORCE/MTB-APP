"""Architecture rules (P12-01 / P14-05).

The domain and application layers know nothing about a database, a web
framework, or an AI provider. Only ``app.persistence`` may import a driver.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN = {
    "domain": {"pymongo", "motor", "bson", "sqlalchemy", "alembic", "fastapi", "starlette", "anthropic", "openai"},
    "application": {"pymongo", "motor", "bson", "sqlalchemy", "alembic", "fastapi", "starlette"},
}


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


@pytest.mark.parametrize("layer", sorted(FORBIDDEN))
def test_layer_imports_no_infrastructure(layer):
    offenders = {}
    for path in sorted((APP / layer).rglob("*.py")):
        bad = imported_roots(path) & FORBIDDEN[layer]
        if bad:
            offenders[str(path.relative_to(APP))] = sorted(bad)
    assert not offenders, f"{layer} must not import infrastructure: {offenders}"


def test_object_id_never_appears_in_domain_or_application():
    hits = [
        str(path.relative_to(APP))
        for layer in ("domain", "application")
        for path in (APP / layer).rglob("*.py")
        if "ObjectId" in path.read_text(encoding="utf-8")
    ]
    assert hits == []


def test_only_the_persistence_package_touches_the_uctsm_mongo_driver():
    allowed = APP / "persistence"
    offenders = []
    for path in APP.rglob("*.py"):
        if allowed in path.parents or "remote_audit" in path.parts:
            continue
        if {"pymongo"} & imported_roots(path):
            offenders.append(str(path.relative_to(APP)))
    assert offenders == []
