"""P3-03: completeness. A UCTSM route cannot exist without an authorization
declaration, and a declaration cannot name a route that does not exist."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI

from _authz_fixtures import permissive_authorizer
from app.api.uctsm import create_uctsm_router
from app.api.uctsm_mongo import create_uctsm_mongo_router
from app.authorization.catalog import CATALOG, Capability
from app.authorization.manifest import MANIFEST
from app.authorization.policy import ResourceKind


async def _user():
    return {}


def _runtime_routes() -> set[tuple[str, str]]:
    authorizer = permissive_authorizer("u", "o")
    app = FastAPI()
    app.include_router(create_uctsm_router(_user, authorizer=authorizer))
    app.include_router(create_uctsm_mongo_router(_user, authorizer=authorizer))
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/uctsm"):
            for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
                found.add((method, path))
    return found


def test_every_registered_uctsm_route_has_a_declaration():
    routes = _runtime_routes()
    assert len(routes) == 76, f"route count changed: {len(routes)} - declare the new route"
    missing = sorted(routes - set(MANIFEST))
    assert not missing, f"routes with no authorization declaration: {missing}"


def test_every_declaration_names_a_real_route():
    stale = sorted(set(MANIFEST) - _runtime_routes())
    assert not stale, f"declarations for routes that no longer exist: {stale}"


def test_declarations_are_well_formed():
    for key, rule in MANIFEST.items():
        assert rule.capabilities, key
        for capability in rule.capabilities:
            assert capability in CATALOG, (key, capability)
        if rule.resource is not ResourceKind.NONE and not rule.list_scoped:
            assert rule.params, f"{key} names a resource but no path parameter"
            for name in rule.params:
                assert "{" + name + "}" in key[1], (key, name)
        if rule.body_field:
            assert rule.body_map and set(rule.body_map.values()) <= set(rule.capabilities), key


def test_every_state_changing_route_requires_a_capability_not_held_by_every_member():
    """No mutation is guarded only by 'is authenticated': each one maps to a
    capability, and the sensitive ones have no role default at all."""
    sensitive = {Capability.SCHEDULE_CORRECT, Capability.SCHEDULE_REVIEW, Capability.SCHEDULE_APPROVE,
                 Capability.SCHEDULE_REJECT, Capability.TRIAL_READ_MODE_SET, Capability.PARITY_ACCEPT,
                 Capability.DEMO_SEED}
    for key, rule in MANIFEST.items():
        if key[0] in {"POST", "PUT", "PATCH", "DELETE"}:
            assert rule.mutation or rule.capability, key
    for capability in sensitive:
        assert not CATALOG[capability].default_roles, capability


def test_all_approval_paths_are_declared_with_the_approve_capability():
    approval_paths = {
        ("POST", "/api/uctsm/schedule-versions/{schedule_version_id}/approve-version"),
        ("POST", "/api/uctsm/schedule-versions/{schedule_version_id}/approve-table"),
    }
    for key in approval_paths:
        assert MANIFEST[key].capabilities == (Capability.SCHEDULE_APPROVE,)
    review = MANIFEST[("POST", "/api/uctsm/schedule-versions/{schedule_version_id}/review")]
    assert Capability.SCHEDULE_APPROVE in review.capabilities
    assert review.body_map == {"APPROVE": Capability.SCHEDULE_APPROVE,
                               "REJECT": Capability.SCHEDULE_REJECT}


def test_server_level_uctsm_entry_points_are_guarded_in_source():
    """server.py hosts the identity and grants endpoints. Assert each one is
    wired to the policy (static check; behaviour is covered by the workflow and
    grants tests)."""
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    for needle in (
        "_identity_reviewer(user)",
        "_require_uctsm_capability(user, authz_catalog.Capability.GRANTS_MANAGE)",
        "create_uctsm_router(current_uctsm_user, authorizer=uctsm_authorizer)",
        "create_uctsm_mongo_router(\n    current_uctsm_user, authorizer=uctsm_authorizer,\n    operational_provisioner=",
    ):
        assert needle in source, needle
