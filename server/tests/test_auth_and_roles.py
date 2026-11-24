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
import datetime
import time
import jwt
import pytest
from app.config import JWT_SECRET
from app.main import app
from app.models.assignment import Assignment
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
    ("POST", "/api/auth/logout"): "public",
    ("GET", "/api/health"): "public",
    ("POST", "/api/ingest"): "public",
    # ANY LOGGED IN USER (even while force_pw_change)
    ("GET", "/api/auth/me"): "any_authenticated",
    ("POST", "/api/auth/change-password"): "any_authenticated",
    ("GET", "/api/me/shifts"): "any_authenticated",
    ("GET", "/api/me/profile"): "any_authenticated",
    # ANY ROLE (scoped to allowed mines)
    ("GET", "/api/mines"): "any_role_scoped",
    ("GET", "/api/mines/{mine_id}/tunnels"): "any_role_scoped",
    ("GET", "/api/nodes"): "any_role_scoped",
    ("GET", "/api/placements"): "any_role_scoped",
    # SUPERIORS ONLY (scoped to allowed mines)
    ("GET", "/api/gateways"): "superiors_scoped",
    ("GET", "/api/history"): "superiors_scoped",
    ("GET", "/api/unplaced"): "superiors_scoped",
    ("PATCH", "/api/nodes/{node_id}/placement"): "superiors_scoped",
    ("DELETE", "/api/nodes/{node_id}/placement"): "superiors_scoped",
    ("GET", "/api/export.xlsx"): "superiors_scoped",
    ("GET", "/api/alerts"): "superiors_scoped",
    ("POST", "/api/alerts/{alert_id}/ack"): "superiors_scoped",
    ("GET", "/api/notifications/config"): "superiors_scoped",
    ("POST", "/api/notifications/config"): "superiors_scoped",
    ("POST", "/api/notifications/test"): "superiors_scoped",
    ("GET", "/api/workers"): "superiors_scoped",
    ("GET", "/api/workers/{worker_id}"): "superiors_scoped",
    ("GET", "/api/assignments"): "superiors_scoped",
    ("POST", "/api/assignments"): "superiors_scoped",
    ("PATCH", "/api/assignments/{assignment_id}"): "superiors_scoped",
    ("DELETE", "/api/assignments/{assignment_id}"): "superiors_scoped",
    # MANAGER ONLY
    ("POST", "/api/mines"): "manager_only",
    ("POST", "/api/mines/{mine_id}/tunnels"): "manager_only",
    ("POST", "/api/mines/{mine_id}/gateways"): "manager_only",
    ("GET", "/api/mines/{mine_id}/gateways"): "manager_only",
    ("PATCH", "/api/gateways/{gateway_id}"): "manager_only",
    ("POST", "/api/gateways/{gateway_id}/rotate-key"): "manager_only",
    ("DELETE", "/api/gateways/{gateway_id}"): "manager_only",
    ("GET", "/api/users"): "manager_only",
    ("POST", "/api/users"): "manager_only",
    ("PATCH", "/api/users/{user_id}"): "manager_only",
    ("DELETE", "/api/users/{user_id}"): "manager_only",
    ("PUT", "/api/workers/{worker_id}/profile"): "manager_only",
    ("POST", "/api/workers/{worker_id}/badge"): "manager_only",
    ("DELETE", "/api/workers/{worker_id}/badge"): "manager_or_foreman",
    # SCANNERS (Manager, Safety Officer, Engineer, Foreman; Miners get 403)
    ("POST", "/api/checkins"): "scanners",
    ("POST", "/api/checkins/manual"): "scanners",
    ("GET", "/api/checkins"): "scanners",
    ("GET", "/api/headcount"): "scanners",
    ("GET", "/api/check-flags"): "scanners",
    ("PATCH", "/api/check-flags/{flag_id}"): "scanners",
    # STEP 7: INCIDENTS, ROLL CALL, AND NODE COMMANDS
    ("POST", "/api/incidents"): "superiors_scoped",
    ("GET", "/api/incidents"): "scanners",
    ("GET", "/api/incidents/{id}"): "scanners",
    ("GET", "/api/incidents/{id}/roster"): "scanners",
    ("POST", "/api/incidents/{id}/roster/{user_id}/account"): "scanners",
    ("PATCH", "/api/incidents/{id}"): "superiors_scoped",
    ("POST", "/api/nodes/{node_id}/commands"): "superiors_scoped",
    ("GET", "/api/nodes/{node_id}/commands"): "superiors_scoped",
    # STEP 8: WORKER REPORTS AND SOS
    ("POST", "/api/reports"): "any_role_scoped",
    ("GET", "/api/reports"): "any_role_scoped",
    ("PATCH", "/api/reports/{id}"): "superiors_scoped",
    # STEP 9: MINE PROFILE AND PRODUCTION
    ("GET", "/api/mines/{id}/profile"): "any_role_scoped",
    ("PATCH", "/api/mines/{id}/profile"): "superiors_scoped",
    ("PUT", "/api/mines/{id}/contacts"): "superiors_scoped",
    ("GET", "/api/mines/{id}/headcount"): "superiors_scoped",
    ("GET", "/api/mines/{id}/production"): "superiors_scoped",
    ("PUT", "/api/mines/{id}/production"): "superiors_scoped",
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
# DATA-DRIVEN MATRIX ROLE ENFORCEMENT TESTS
# =============================================================================

def resolve_test_path(path: str) -> str:
    """Replaces URL path parameters with dummy values for matrix routing assertions."""
    import re
    def _repl(match):
        param = match.group(1)
        if param in ("gateway", "mac", "gateway_mac"):
            return "AA:BB:CC:DD:EE:FF"
        return "1"
    return re.sub(r"\{([^}]+)\}", _repl, path)


ROLE_PERMISSIONS = {
    "public": {"anonymous", ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MINER},
    "any_authenticated": {ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MINER},
    "any_role_scoped": {ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MINER},
    "superiors_scoped": {ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER},
    "scanners": {ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN},
    "manager_or_foreman": {ROLE_MANAGER, ROLE_FOREMAN},
    "manager_or_safety_officer": {ROLE_MANAGER, ROLE_SAFETY_OFFICER},
    "manager_only": {ROLE_MANAGER},
}

ALL_ROLES = [ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MINER]


def test_data_driven_route_policy_matrix(client, db):
    """Data-driven test strictly enforcing access policies from POLICY_TABLE for all roles."""
    comp = Company(name="Policy Test Coal", name_normalized="policy test coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Policy Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    users = {}
    for role in ALL_ROLES:
        u = create_user_with_role(db, comp.id, f"usr_{role}", role)
        db.add(UserMine(user_id=u.id, mine_id=mine.id))
        users[role] = u
    db.commit()

    from unittest.mock import patch

    with patch("app.routers.demo.simulator_instance.step", return_value=7):
        for (method, path), policy in POLICY_TABLE.items():
            resolved_path = resolve_test_path(path)
            allowed = ROLE_PERMISSIONS[policy]

            # 1. Anonymous request
            client.cookies.clear()
            resp = client.request(method, resolved_path, json={})
            if policy == "public":
                assert resp.status_code not in (401, 403), (
                    f"Anonymous was rejected on public route {method} {path}: {resp.status_code}"
                )
            else:
                assert resp.status_code == 401, (
                    f"Anonymous did not receive 401 on protected route {method} {path}: got {resp.status_code}"
                )

            # 2. Authenticated requests per role
            for role in ALL_ROLES:
                login_client(client, users[role].id)
                resp = client.request(method, resolved_path, json={})
                if role in allowed:
                    assert resp.status_code not in (401, 403), (
                        f"Role '{role}' unexpectedly rejected with {resp.status_code} on allowed route {method} {path}"
                    )
                else:
                    assert resp.status_code == 403, (
                        f"Role '{role}' was not forbidden (403) on route {method} {path}: got {resp.status_code}"
                    )


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
    t_b = Tunnel(mine_id=mine_b.id, name="Tunnel B", code="TUN-B")
    db.add(t_b)
    db.commit()
    place_payload = {
        "tunnel_id": t_b.id,
        "lat": 23.0,
        "lon": 85.0,
    }
    assert client.patch(f"/api/nodes/{node_b.id}/placement", json=place_payload).status_code == 404
    assert client.delete(f"/api/nodes/{node_b.id}/placement").status_code == 404


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

    t = Tunnel(mine_id=m.id, name="Tunnel 1", code="TUN-1")
    db.add(t)
    db.flush()

    gw = Gateway(mac="33:33:33:33:33:33", mine_id=m.id, name="GW 3", active=True)
    db.add(gw)
    db.flush()

    node = Node(gateway_id=gw.id, node_no=1, status="normal", tunnel_id=t.id, lat=23.5, lon=85.5)
    db.add(node)
    db.commit()

    # Miner view (assigned to tunnel 1)
    now = datetime.datetime.now(datetime.timezone.utc)
    miner = create_user_with_role(db, comp.id, "miner_view", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=m.id))
    db.add(
        Assignment(
            user_id=miner.id,
            tunnel_id=t.id,
            starts_at=now - datetime.timedelta(hours=1),
            ends_at=now + datetime.timedelta(hours=7),
        )
    )
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


def test_worker_placements_field_leakage_protection(client, db):
    """A2: Worker callers of GET /api/placements receive ONLY worker-safe fields."""
    comp = Company(name="Placements Leakage Test", name_normalized="placements leakage test")
    db.add(comp)
    db.flush()

    m = Mine(company_id=comp.id, name="Placements Mine", status="active", method="underground")
    db.add(m)
    db.flush()

    t = Tunnel(mine_id=m.id, name="Placements Tunnel", code="TUN-PL")
    db.add(t)
    db.flush()

    gw = Gateway(mac="44:44:44:44:44:44", mine_id=m.id, name="GW 4", active=True)
    db.add(gw)
    db.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    mgr = create_user_with_role(db, comp.id, "pl_mgr", ROLE_MANAGER)
    miner = create_user_with_role(db, comp.id, "pl_miner", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=m.id))
    db.add(
        Assignment(
            user_id=miner.id,
            tunnel_id=t.id,
            starts_at=now - datetime.timedelta(hours=1),
            ends_at=now + datetime.timedelta(hours=7),
        )
    )
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=9,
        tunnel_id=t.id,
        lat=23.7,
        lon=86.4,
        placed_by=mgr.id,
        status="normal",
    )
    db.add(node)
    db.commit()

    # 1. Miner request: only node_id, location_name, latitude, longitude, placed_at
    login_client(client, miner.id)
    miner_resp = client.get("/api/placements")
    assert miner_resp.status_code == 200
    miner_placements = miner_resp.json()["placements"]
    assert len(miner_placements) == 1
    p = miner_placements[0]
    expected_keys = {"node_id", "location_name", "latitude", "longitude", "placed_at"}
    assert set(p.keys()) == expected_keys
    assert "gateway_mac_address" not in p
    assert "placed_by" not in p

    # 2. Manager request: keeps gateway_mac_address and placed_by
    login_client(client, mgr.id)
    mgr_resp = client.get("/api/placements")
    assert mgr_resp.status_code == 200
    mgr_placements = mgr_resp.json()["placements"]
    assert len(mgr_placements) == 1
    mp = mgr_placements[0]
    assert "gateway_mac_address" in mp
    assert mp["gateway_mac_address"] == "44:44:44:44:44:44"
    assert mp["placed_by"] == mgr.id


def test_worker_node_extra_field_forbidden():
    """A6: WorkerNodeView and NodesResponse reject unexpected fields."""
    from pydantic import ValidationError
    from app.schemas.nodes import WorkerNodeView, NodesResponse

    # Extra field raises ValidationError
    with pytest.raises(ValidationError):
        WorkerNodeView(
            node_id=1,
            location_name="Test",
            latitude=23.0,
            longitude=86.0,
            status="normal",
            color="#2d7a45",
            tilt_x=0.0,
            tilt_y=0.0,
            vibration_magnitude=0.01,
            vibration_detected=False,
            battery_percentage=90.0,
            low_battery=False,
            online=True,
            age_s=5.0,
            unexpected_leaked_field="leak",  # must fail extra="forbid"
        )


def test_password_policy_enforcement(client, db):
    """A4: Password policy rejects short, matching, or CHANGE_ME passwords."""
    from app.security.password_policy import validate_new_password

    # Direct policy unit tests
    with pytest.raises(ValueError, match="at least 10 characters"):
        validate_new_password("Short1!", "user1")
    with pytest.raises(ValueError, match="cannot match username"):
        validate_new_password("TestUsername123", "testusername123")
    with pytest.raises(ValueError, match="CHANGE_ME"):
        validate_new_password("CHANGE_ME_NOW_1234", "user1")

    # Valid password passes
    validate_new_password("CompletelySecure@2026", "user1")

    # API route integration: POST /api/users
    comp = Company(name="Policy API Corp", name_normalized="policy api corp")
    db.add(comp)
    db.flush()
    mgr = create_user_with_role(db, comp.id, "mgr_pol", ROLE_MANAGER)
    login_client(client, mgr.id)

    # Short password -> 400
    bad_resp = client.post(
        "/api/users",
        json={
            "username": "new_sub_user",
            "full_name": "New Sub",
            "role": "engineer",
            "initial_password": "short",
        },
    )
    assert bad_resp.status_code == 400
    assert "at least 10 characters" in bad_resp.json()["detail"]

    # CHANGE_ME password -> 400
    change_me_resp = client.post(
        "/api/users",
        json={
            "username": "new_sub_user2",
            "full_name": "New Sub",
            "role": "engineer",
            "initial_password": "CHANGE_ME_PASSWORD_123",
        },
    )
    assert change_me_resp.status_code == 400
    assert "CHANGE_ME" in change_me_resp.json()["detail"]


def test_logout_always_succeeds_without_active_session(client):
    """A6: POST /api/auth/logout always clears cookie and returns 200 without requiring valid session."""
    # 1. Anonymous with no cookie
    client.cookies.clear()
    resp1 = client.post("/api/auth/logout")
    assert resp1.status_code == 200
    assert resp1.json() == {"message": "Logged out"}

    # 2. Tampered cookie
    client.cookies.set(COOKIE_NAME, "tampered.jwt.cookie")
    resp2 = client.post("/api/auth/logout")
    assert resp2.status_code == 200
    assert resp2.json() == {"message": "Logged out"}


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
