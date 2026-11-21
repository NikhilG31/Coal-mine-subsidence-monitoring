"""Comprehensive test suite for authentication, RBAC, tenant isolation, and session security.

Tests:
1. Complete Route Policy Table verification (fails if any /api route is unregistered).
2. Role access matrix (401 for no cookie, 403 for insufficient role).
3. Multi-tenant company isolation (404 for out-of-scope company resources).
4. Mine-level scoping (assigned mines vs all company mines).
5. Force-password change gate and password update flow.
6. Immediate session invalidation on user deactivation.
7. Tampered and expired JWT rejection.
8. In-memory login attempt rate limiting (429 with Retry-After).
9. Legacy PBKDF2 hash verification and automatic rehashing.
10. WorkerNodeView field leakage prevention.
11. Hierarchy protections (cannot create manager, cannot deactivate self/last manager).
"""
import time
import jwt
import pytest
from app.config import JWT_SECRET
from app.main import app
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.security.passwords import hash_password, verify_password
from app.security.rate_limit import clear_all_rate_limits
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_MINER,
    ROLE_SAFETY_OFFICER,
    SUPERIOR_ROLES,
    WORKER_ROLES,
)
from app.security.tokens import COOKIE_NAME, create_session_token


# =============================================================================
# ROUTE POLICY SPECIFICATION TABLE
# =============================================================================

POLICY_TABLE = {
    # PUBLIC ROUTES
    ("POST", "/api/auth/login"): "public",
    ("GET", "/api/health"): "public",
    ("POST", "/api/ingest"): "public",
    # ANY LOGGED IN USER (even while force_pw_change)
    ("GET", "/api/auth/me"): "any_authenticated",
    ("POST", "/api/auth/logout"): "any_authenticated",
    ("POST", "/api/auth/change-password"): "any_authenticated",
    # ANY ROLE (scoped to allowed mines)
    ("GET", "/api/nodes"): "any_role_scoped",
    ("GET", "/api/placements"): "any_role_scoped",
    # SUPERIORS ONLY (scoped to allowed mines)
    ("GET", "/api/gateways"): "superiors_scoped",
    ("GET", "/api/history"): "superiors_scoped",
    ("GET", "/api/unplaced"): "superiors_scoped",
    ("POST", "/api/placements"): "superiors_scoped",
    ("DELETE", "/api/placements/{gateway}/{node_id}"): "superiors_scoped",
    ("GET", "/api/export.xlsx"): "superiors_scoped",
    ("GET", "/api/alerts"): "superiors_scoped",
    ("POST", "/api/alerts/{alert_id}/ack"): "superiors_scoped",
    ("GET", "/api/notifications/config"): "superiors_scoped",
    ("POST", "/api/notifications/config"): "superiors_scoped",
    ("POST", "/api/notifications/test"): "superiors_scoped",
    # MANAGER ONLY
    ("GET", "/api/users"): "manager_only",
    ("POST", "/api/users"): "manager_only",
    ("PATCH", "/api/users/{user_id}"): "manager_only",
    ("DELETE", "/api/users/{user_id}"): "manager_only",
    ("POST", "/api/demo/seed"): "manager_only",
    ("POST", "/api/demo/step"): "manager_only",
    ("POST", "/api/demo/toggle-live"): "manager_only",
    ("GET", "/api/demo/status"): "manager_only",
    ("POST", "/api/demo/trigger-hazard"): "manager_only",
}


def test_every_api_route_in_policy_table():
    """Asserts every /api endpoint registered on the FastAPI app is documented in POLICY_TABLE."""
    registered_api_routes = set()
    for route in app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path and path.startswith("/api"):
            for m in methods:
                if m not in ("HEAD", "OPTIONS"):
                    registered_api_routes.add((m, path))

    policy_routes = set(POLICY_TABLE.keys())
    missing_from_policy = registered_api_routes - policy_routes
    assert not missing_from_policy, f"Unprotected routes not listed in POLICY_TABLE: {missing_from_policy}"


# =============================================================================
# TEST FIXTURES & HELPERS
# =============================================================================

def create_user_with_role(db, company_id: int, username: str, role: str, force_pw: bool = False):
    u = User(
        company_id=company_id,
        username=username,
        full_name=f"Test {username}",
        role=role,
        password_hash=hash_password("SecretPass@2026"),
        active=True,
        force_pw_change=force_pw,
    )
    db.add(u)
    db.commit()
    return u


def login_client(client, user_id: int):
    token = create_session_token(user_id)
    client.cookies.set(COOKIE_NAME, token)


# =============================================================================
# MATRIX ROLE ENFORCEMENT TESTS
# =============================================================================

def test_superiors_only_matrix(client, db):
    """Verifies that superiors-only routes reject unauthenticated requests with 401 and workers with 403."""
    comp = Company(name="Coal India", name_normalized="coal india")
    db.add(comp)
    db.flush()

    manager = create_user_with_role(db, comp.id, "mgr", ROLE_MANAGER)
    officer = create_user_with_role(db, comp.id, "safety", ROLE_SAFETY_OFFICER)
    engineer = create_user_with_role(db, comp.id, "eng", ROLE_ENGINEER)
    foreman = create_user_with_role(db, comp.id, "foreman", ROLE_FOREMAN)
    miner = create_user_with_role(db, comp.id, "miner", ROLE_MINER)

    # Clean cookies -> 401
    client.cookies.clear()
    assert client.get("/api/gateways").status_code == 401
    assert client.get("/api/history").status_code == 401
    assert client.get("/api/unplaced").status_code == 401
    assert client.get("/api/alerts").status_code == 401
    assert client.get("/api/export.xlsx").status_code == 401
    assert client.post("/api/notifications/test").status_code == 401

    # Worker roles -> 403
    for worker in (foreman, miner):
        login_client(client, worker.id)
        assert client.get("/api/gateways").status_code == 403
        assert client.get("/api/history").status_code == 403
        assert client.get("/api/unplaced").status_code == 403
        assert client.get("/api/alerts").status_code == 403
        assert client.get("/api/export.xlsx").status_code == 403
        assert client.post("/api/notifications/test").status_code == 403

    # Superiors -> permitted (or 501 for notifications/test stub)
    for superior in (manager, officer, engineer):
        login_client(client, superior.id)
        assert client.get("/api/gateways").status_code == 200
        assert client.get("/api/history").status_code == 200
        assert client.get("/api/unplaced").status_code == 200
        assert client.get("/api/alerts").status_code == 200
        assert client.post("/api/notifications/test").status_code == 501


def test_manager_only_matrix(client, db):
    """Verifies that manager-only endpoints reject safety officers and engineers with 403."""
    comp = Company(name="Western Coalfields", name_normalized="western coalfields")
    db.add(comp)
    db.flush()

    manager = create_user_with_role(db, comp.id, "w_mgr", ROLE_MANAGER)
    officer = create_user_with_role(db, comp.id, "w_officer", ROLE_SAFETY_OFFICER)
    engineer = create_user_with_role(db, comp.id, "w_eng", ROLE_ENGINEER)

    # Superiors who are not managers get 403
    for sub in (officer, engineer):
        login_client(client, sub.id)
        assert client.get("/api/users").status_code == 403
        assert client.post("/api/users", json={}).status_code == 403
        assert client.patch(f"/api/users/{sub.id}", json={}).status_code == 403
        assert client.delete(f"/api/users/{sub.id}").status_code == 403
        assert client.get("/api/demo/status").status_code == 403

    # Manager gets 200
    login_client(client, manager.id)
    assert client.get("/api/users").status_code == 200
    assert client.get("/api/demo/status").status_code == 200


# =============================================================================
# MULTI-TENANT COMPANY ISOLATION TESTS
# =============================================================================

def test_company_tenant_isolation(client, db):
    """Verifies that Company A cannot observe or modify Company B's gateways or nodes."""
    comp_a = Company(name="Company Alpha", name_normalized="company alpha")
    comp_b = Company(name="Company Beta", name_normalized="company beta")
    db.add_all([comp_a, comp_b])
    db.flush()

    mine_a = Mine(company_id=comp_a.id, name="Mine A", status="active", method="underground")
    mine_b = Mine(company_id=comp_b.id, name="Mine B", status="active", method="underground")
    db.add_all([mine_a, mine_b])
    db.flush()

    gw_a = Gateway(mac="AA:AA:AA:AA:AA:AA", mine_id=mine_a.id, name="Gateway A", active=True)
    gw_b = Gateway(mac="BB:BB:BB:BB:BB:BB", mine_id=mine_b.id, name="Gateway B", active=True)
    db.add_all([gw_a, gw_b])
    db.flush()

    node_b = Node(gateway_id=gw_b.id, node_no=5, status="normal")
    db.add(node_b)
    db.commit()

    manager_a = create_user_with_role(db, comp_a.id, "mgr_a", ROLE_MANAGER)
    login_client(client, manager_a.id)

    # Company A's manager listing gateways sees only Gateway A
    gw_res = client.get("/api/gateways").json()
    macs = [g["gateway_mac_address"] for g in gw_res["gateways"]]
    assert "AA:AA:AA:AA:AA:AA" in macs
    assert "BB:BB:BB:BB:BB:BB" not in macs

    # Accessing Company B's gateway in history returns 404 (does not reveal existence)
    assert client.get(f"/api/history?gateway={gw_b.mac}").status_code == 404

    # Placing or unplacing Company B's node returns 404
    place_payload = {
        "gateway_mac_address": gw_b.mac,
        "node_id": 5,
        "latitude": 23.0,
        "longitude": 85.0,
    }
    assert client.post("/api/placements", json=place_payload).status_code == 404
    assert client.delete(f"/api/placements/{gw_b.mac}/5").status_code == 404


# =============================================================================
# MINE-LEVEL SCOPING TESTS
# =============================================================================

def test_mine_scoping_rules(client, db):
    """Verifies that users only observe telemetry belonging to their permitted mines."""
    comp = Company(name="Central Collieries", name_normalized="central collieries")
    db.add(comp)
    db.flush()

    m1 = Mine(company_id=comp.id, name="Mine 1", status="active", method="underground")
    m2 = Mine(company_id=comp.id, name="Mine 2", status="active", method="underground")
    db.add_all([m1, m2])
    db.flush()

    gw1 = Gateway(mac="11:11:11:11:11:11", mine_id=m1.id, name="GW 1", active=True)
    gw2 = Gateway(mac="22:22:22:22:22:22", mine_id=m2.id, name="GW 2", active=True)
    db.add_all([gw1, gw2])
    db.flush()

    n1 = Node(gateway_id=gw1.id, node_no=1, status="normal")
    n2 = Node(gateway_id=gw2.id, node_no=2, status="normal")
    db.add_all([n1, n2])
    db.commit()

    # Safety officer assigned strictly to Mine 1
    officer = create_user_with_role(db, comp.id, "officer_m1", ROLE_SAFETY_OFFICER)
    db.add(UserMine(user_id=officer.id, mine_id=m1.id))
    db.commit()

    login_client(client, officer.id)
    nodes_res = client.get("/api/nodes").json()
    assert len(nodes_res["nodes"]) == 1
    assert nodes_res["nodes"][0]["node_id"] == 1

    # Manager with NO user_mines rows sees BOTH mines
    manager_all = create_user_with_role(db, comp.id, "mgr_all", ROLE_MANAGER)
    login_client(client, manager_all.id)
    mgr_nodes = client.get("/api/nodes").json()
    assert len(mgr_nodes["nodes"]) == 2

    # Manager WITH specific user_mines rows sees ONLY assigned mines
    manager_scoped = create_user_with_role(db, comp.id, "mgr_scoped", ROLE_MANAGER)
    db.add(UserMine(user_id=manager_scoped.id, mine_id=m2.id))
    db.commit()
    login_client(client, manager_scoped.id)
    mgr_scoped_nodes = client.get("/api/nodes").json()
    assert len(mgr_scoped_nodes["nodes"]) == 1
    assert mgr_scoped_nodes["nodes"][0]["node_id"] == 2


# =============================================================================
# FORCE PASSWORD CHANGE GATE TESTS
# =============================================================================

def test_force_password_change_flow(client, db):
    """Verifies that force_pw_change blocks general API access until password is changed."""
    comp = Company(name="South Eastern Coal", name_normalized="south eastern coal")
    db.add(comp)
    db.flush()

    user = create_user_with_role(db, comp.id, "fresh_user", ROLE_ENGINEER, force_pw=True)
    login_client(client, user.id)

    # Blocked on protected routes with code "password_change_required"
    resp = client.get("/api/nodes")
    assert resp.status_code == 403
    assert resp.json().get("code") == "password_change_required"

    # Permitted on /api/auth/me
    me_resp = client.get("/api/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["user"]["force_pw_change"] is True

    # Change password
    change_resp = client.post(
        "/api/auth/change-password",
        json={
            "current_password": "SecretPass@2026",
            "new_password": "CompletelyNewPassword@2026",
        },
    )
    assert change_resp.status_code == 200

    # User can now access protected routes
    assert client.get("/api/nodes").status_code == 200


# =============================================================================
# SESSION & USER DEACTIVATION TESTS
# =============================================================================

def test_deactivated_user_session_invalidated(client, db):
    """Verifies that deactivating a user immediately causes active session cookies to return 401."""
    comp = Company(name="Deact Test Corp", name_normalized="deact test corp")
    db.add(comp)
    db.flush()

    user = create_user_with_role(db, comp.id, "temp_worker", ROLE_MINER)
    login_client(client, user.id)
    assert client.get("/api/nodes").status_code == 200

    # Deactivate user in database
    user.active = False
    db.commit()

    # Immediate rejection
    assert client.get("/api/nodes").status_code == 401


def test_tampered_and_expired_jwt(client):
    """Verifies rejection of tampered or expired JWT cookies."""
    # Tampered cookie
    client.cookies.set(COOKIE_NAME, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature")
    assert client.get("/api/auth/me").status_code == 401

    # Expired token
    expired_payload = {
        "sub": "1",
        "iat": int(time.time()) - 4000,
        "exp": int(time.time()) - 1000,
    }
    expired_token = jwt.encode(expired_payload, JWT_SECRET, algorithm="HS256")
    client.cookies.set(COOKIE_NAME, expired_token)
    assert client.get("/api/auth/me").status_code == 401


# =============================================================================
# RATE LIMITING & HASH REHASH TESTS
# =============================================================================

def test_login_rate_limiting(client):
    """Verifies that 5 failed logins return 429 with Retry-After header."""
    clear_all_rate_limits()
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": "test_rl", "password": "wrong"})
        assert resp.status_code == 401

    # 6th attempt is rate-limited
    res_429 = client.post("/api/auth/login", json={"username": "test_rl", "password": "wrong"})
    assert res_429.status_code == 429
    assert "Retry-After" in res_429.headers
    clear_all_rate_limits()


def test_legacy_hash_verification_and_rehash(client, db):
    """Verifies that legacy step 1 hashes (<salt>$<hash>, 100k iter) authenticate and rehash to new standard."""
    comp = Company(name="Legacy Hash Corp", name_normalized="legacy hash corp")
    db.add(comp)
    db.flush()

    # Construct legacy format: <salt>$<hash> (100k iter with salt.encode("utf-8"))
    import hashlib
    salt = "1234567890abcdef"
    legacy_hash_raw = hashlib.pbkdf2_hmac("sha256", b"OldPassword@2026", salt.encode("utf-8"), 100_000).hex()
    legacy_stored = f"{salt}${legacy_hash_raw}"

    user = User(
        company_id=comp.id,
        username="legacy_mgr",
        full_name="Legacy Manager",
        role=ROLE_MANAGER,
        password_hash=legacy_stored,
        active=True,
    )
    db.add(user)
    db.commit()

    # Login with legacy credentials
    resp = client.post("/api/auth/login", json={"username": "legacy_mgr", "password": "OldPassword@2026"})
    assert resp.status_code == 200

    # Verify password was rehashed to new 600,000-iteration standard
    db.refresh(user)
    assert user.password_hash.startswith("pbkdf2_sha256$600000$")
    assert verify_password("OldPassword@2026", user.password_hash)


# =============================================================================
# WORKER MODEL & HIERARCHY TESTS
# =============================================================================

def test_worker_node_view_field_leakage_protection(client, db):
    """Spec B6: WorkerNodeView must NOT carry gateway MAC, counter, score, reasons, or placed_by."""
    comp = Company(name="Model Leakage Test", name_normalized="model leakage test")
    db.add(comp)
    db.flush()

    m = Mine(company_id=comp.id, name="Test Mine", status="active", method="underground")
    db.add(m)
    db.flush()

    gw = Gateway(mac="33:33:33:33:33:33", mine_id=m.id, name="GW 3", active=True)
    db.add(gw)
    db.flush()

    node = Node(gateway_id=gw.id, node_no=1, status="normal", lat=23.5, lon=85.5)
    db.add(node)
    db.commit()

    # Miner view
    miner = create_user_with_role(db, comp.id, "miner_view", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=m.id))
    db.commit()

    login_client(client, miner.id)
    worker_res = client.get("/api/nodes").json()
    assert len(worker_res["nodes"]) == 1
    w_node = worker_res["nodes"][0]

    # Permitted fields
    assert "node_id" in w_node
    assert "location_name" in w_node
    assert "status" in w_node
    assert "tilt_x" in w_node

    # Forbidden fields MUST NOT exist
    forbidden_keys = {"gateway_mac_address", "gateway", "counter", "score", "reasons", "placed_by"}
    for k in forbidden_keys:
        assert k not in w_node, f"Security leakage: Forbidden field '{k}' exposed to worker"


def test_user_hierarchy_protections(client, db):
    """Verifies that Managers cannot create managers, cannot demote/deactivate themselves or the last manager."""
    comp = Company(name="Hierarchy Corp", name_normalized="hierarchy corp")
    db.add(comp)
    db.flush()

    manager = create_user_with_role(db, comp.id, "sole_mgr", ROLE_MANAGER)
    login_client(client, manager.id)

    # Cannot create another manager
    bad_create = client.post(
        "/api/users",
        json={
            "username": "second_mgr",
            "full_name": "Second Manager",
            "role": "manager",
            "initial_password": "ValidPassword@123",
        },
    )
    assert bad_create.status_code == 403

    # Cannot deactivate self
    deact_self = client.delete(f"/api/users/{manager.id}")
    assert deact_self.status_code == 400

    # Cannot demote self
    demote_self = client.patch(f"/api/users/{manager.id}", json={"role": "safety_officer"})
    assert demote_self.status_code == 400
