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


def test_application_does_not_depend_on_legacy_sql_services_or_api_modules():
    offenders = []
    for path in (APP / "application").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(("app.services", "app.api", "app.db")):
                offenders.append(str(path.relative_to(APP)))
    assert offenders == []


def test_mongo_http_adapters_do_not_import_the_sql_router_for_shared_helpers():
    for name in ("uctsm_mongo.py", "uctsm_amendments.py", "uctsm_authoring.py"):
        tree = ast.parse((APP / "api" / name).read_text(encoding="utf-8"))
        assert not any(isinstance(node, ast.ImportFrom) and node.module == "app.api.uctsm"
                       for node in ast.walk(tree)), name


def test_review_projection_import_and_use_need_no_database_or_provider(tmp_path):
    import subprocess
    import sys
    script = '''
import importlib.abc, sys
class NoInfrastructure(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sqlalchemy', 'pymongo', 'motor', 'bson', 'fastapi', 'anthropic', 'openai'}:
            raise AssertionError('pure review depends on infrastructure: ' + fullname)
sys.meta_path.insert(0, NoInfrastructure())
from app.application.review_projection import build_review_view
from app.domain.schedule.models import UniversalSchedule, ScheduleMetadata
view = build_review_view(UniversalSchedule(schedule_metadata=ScheduleMetadata(name='Synthetic empty draft')))
assert view['approval_blockers']
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=APP.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
