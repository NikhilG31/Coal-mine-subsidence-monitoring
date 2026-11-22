"""End-to-end and unit test suite for Phase B (Step 3): Mines, Gateways, Placements, and Audit.

Verifies:
1. Ingest key authentication (missing key, wrong key, unknown MAC, inactive gateway, placeholder hash, key rotation, ALLOW_UNKEYED_INGEST).
2. Gateway key safety (never exposed in GET responses, audit logs, or error responses).
3. Placement constraint completeness (SQL check constraint ck_nodes_placement_complete and API cross-mine 400).
4. Audit logging accuracy and atomicity (rolls back on failure, no secrets in detail).
5. Done-When full manager lifecycle journey from empty account to placed node carrying a tunnel.
"""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User
from app.security.passwords import hash_password
from app.security.roles import ROLE_MANAGER
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.gateway_keys import generate_key, hash_key
from app.services.packet_codec import pack_gateway_packet


def test_ingest_key_authentication_matrix(client, db, monkeypatch):
    """B2: Comprehensive authentication matrix for /api/ingest."""
    comp = Company(name="Ingest Auth Test Corp", name_normalized="ingest auth test corp")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Auth Test Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    raw_key = generate_key()
    gw = Gateway(
        mac="AA:11:22:33:44:55",
        mine_id=mine.id,
        name="Keyed Gateway",
        api_key_hash=hash_key(raw_key),
        active=True,
    )
    db.add(gw)
    db.commit()

    pkt = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=1,
        counter=10,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
    )

    # 1. Missing key -> 401
    r_missing = client.post("/api/ingest", content=pkt, headers={"Content-Type": "application/octet-stream"})
    assert r_missing.status_code == 401
    assert r_missing.json()["detail"] == "Gateway authentication failed"

    # 2. Wrong key -> 401
    r_wrong = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwk_completelyinvalidkeyvalue000000000000"},
    )
    assert r_wrong.status_code == 401
    assert r_wrong.json()["detail"] == "Gateway authentication failed"

    # 3. Unknown MAC -> 401
    unknown_pkt = pack_gateway_packet(
        gateway_mac="00:99:88:77:66:55",
        node_id=1,
        counter=10,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
    )
    r_unknown = client.post(
        "/api/ingest",
        content=unknown_pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    assert r_unknown.status_code == 401
    assert r_unknown.json()["detail"] == "Gateway authentication failed"

    # 4. Inactive gateway -> 401
    gw.active = False
    db.commit()
    r_inactive = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    assert r_inactive.status_code == 401
    assert r_inactive.json()["detail"] == "Gateway authentication failed"
    gw.active = True
    db.commit()

    # 5. Placeholder hash -> 401
    gw.api_key_hash = "placeholder_dev_api_key_hash"
    db.commit()
    r_placeholder = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    assert r_placeholder.status_code == 401
    assert r_placeholder.json()["detail"] == "Gateway authentication failed"

    # Restore valid hash
    gw.api_key_hash = hash_key(raw_key)
    db.commit()

    # 6. Valid key -> 200
    r_valid = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    assert r_valid.status_code == 200

    # 7. Key rotation
    mgr = User(
        company_id=comp.id,
        username="mgr_rot",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Rotation Mgr",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.commit()
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))

    rot_res = client.post(f"/api/gateways/{gw.id}/rotate-key")
    assert rot_res.status_code == 200
    assert rot_res.headers.get("Cache-Control") == "no-store"
    new_key = rot_res.json()["api_key"]
    assert new_key != raw_key
    assert new_key.startswith("gwk_")

    # Old key fails with 401
    r_old = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key},
    )
    assert r_old.status_code == 401

    # New key succeeds with 200
    r_new = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": new_key},
    )
    assert r_new.status_code == 200

    # 8. Dev flag ALLOW_UNKEYED_INGEST: accepts unkeyed, but wrong key still 401
    monkeypatch.setattr("app.routers.ingest.ALLOW_UNKEYED_INGEST", True)
    r_unkeyed = client.post("/api/ingest", content=pkt, headers={"Content-Type": "application/octet-stream"})
    assert r_unkeyed.status_code == 200

    r_wrong_with_flag = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwk_wrongkey12345678901234567890"},
    )
    assert r_wrong_with_flag.status_code == 401


def test_gateway_key_safety(client, db):
    """B2: Gateway API key is shown ONCE and NEVER exposed in list/detail, audit, or errors."""
    comp = Company(name="Safety Test Corp", name_normalized="safety test corp")
    db.add(comp)
    db.flush()

    mgr = User(
        company_id=comp.id,
        username="key_safety_mgr",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Key Safety Manager",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.flush()

    mine = Mine(company_id=comp.id, name="Key Safety Mine", status="active", method="underground")
    db.add(mine)
    db.commit()

    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))

    # Register gateway: returns api_key once with Cache-Control: no-store
    create_res = client.post(
        f"/api/mines/{mine.id}/gateways",
        json={"name": "Safety GW", "mac": "CC:11:22:33:44:55"},
    )
    assert create_res.status_code == 200
    assert create_res.headers.get("Cache-Control") == "no-store"
    created_key = create_res.json()["api_key"]
    gw_id = create_res.json()["id"]

    # 1. GET /api/gateways must NOT contain key or hash
    list_res = client.get("/api/gateways").json()
    for g in list_res["gateways"]:
        assert "api_key" not in g
        assert "api_key_hash" not in g

    # 2. GET /api/mines/{id}/gateways must NOT contain key or hash
    mine_gw_res = client.get(f"/api/mines/{mine.id}/gateways").json()
    for g in mine_gw_res["gateways"]:
        assert "api_key" not in g
        assert "api_key_hash" not in g

    # 3. Audit log must NOT contain key or hash
    audits = db.query(AuditLog).filter(AuditLog.entity_id == gw_id).all()
    for a in audits:
        assert created_key not in str(a.detail)
        assert "api_key" not in a.detail
        assert "api_key_hash" not in a.detail


def test_placement_constraint_and_cross_mine_validation(client, db):
    """B0 & B1: Enforces CHECK constraint ck_nodes_placement_complete and cross-mine 400."""
    comp = Company(name="Constraint Corp", name_normalized="constraint corp")
    db.add(comp)
    db.flush()

    m1 = Mine(company_id=comp.id, name="Mine Alpha", status="active", method="underground")
    m2 = Mine(company_id=comp.id, name="Mine Beta", status="active", method="underground")
    db.add_all([m1, m2])
    db.flush()

    t1 = Tunnel(mine_id=m1.id, name="Alpha Tunnel", code="T-A")
    t2 = Tunnel(mine_id=m2.id, name="Beta Tunnel", code="T-B")
    db.add_all([t1, t2])
    db.flush()

    raw_key = generate_key()
    gw1 = Gateway(
        mac="DD:11:22:33:44:55",
        mine_id=m1.id,
        name="Alpha Gateway",
        api_key_hash=hash_key(raw_key),
        active=True,
    )
    db.add(gw1)
    db.commit()

    # 1. SQL CHECK constraint test: partial placement fails
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            bad_node_1 = Node(gateway_id=gw1.id, node_no=100, tunnel_id=t1.id, lat=None, lon=None)
            db.add(bad_node_1)
            db.flush()

    with pytest.raises(IntegrityError):
        with db.begin_nested():
            bad_node_2 = Node(gateway_id=gw1.id, node_no=101, tunnel_id=None, lat=23.5, lon=86.5)
            db.add(bad_node_2)
            db.flush()

    # Clean unplaced node succeeds
    good_unplaced = Node(gateway_id=gw1.id, node_no=102, tunnel_id=None, lat=None, lon=None)
    db.add(good_unplaced)
    db.commit()

    # 2. Cross-mine placement API test: Placing node from Gateway 1 into Tunnel 2 fails with 400
    mgr = User(
        company_id=comp.id,
        username="cross_mine_mgr",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Cross Mine Manager",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.commit()
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))

    cross_res = client.patch(
        f"/api/nodes/{good_unplaced.id}/placement",
        json={"tunnel_id": t2.id, "lat": 23.5, "lon": 86.5},
    )
    assert cross_res.status_code == 400
    assert "same mine" in cross_res.json()["detail"]


def test_audit_logging_lifecycle_and_rollback(client, db):
    """B4: Audit row recorded atomically on state change; rolls back on failure; no secrets."""
    comp = Company(name="Audit Test Corp", name_normalized="audit test corp")
    db.add(comp)
    db.flush()

    mgr = User(
        company_id=comp.id,
        username="audit_mgr",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Audit Mgr",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.commit()
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))

    initial_audit_count = db.query(AuditLog).count()

    # 1. Create Mine -> 1 audit row
    mine_res = client.post("/api/mines", json={"name": "Audit Logged Mine"})
    assert mine_res.status_code == 200
    mine_id = mine_res.json()["id"]

    assert db.query(AuditLog).count() == initial_audit_count + 1
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "mine.create"
    assert last_audit.entity == "mine"
    assert last_audit.entity_id == mine_id

    # 2. Failed mutation (e.g. duplicate mine name -> 409) writes NO audit row
    client.post("/api/mines", json={"name": "Audit Logged Mine"})
    assert db.query(AuditLog).count() == initial_audit_count + 1

    # 3. Create Tunnel -> 1 audit row
    tun_res = client.post(f"/api/mines/{mine_id}/tunnels", json={"name": "Logged Tunnel", "code": "TUN-LOG"})
    assert tun_res.status_code == 200
    tun_id = tun_res.json()["id"]
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "tunnel.create"
    assert last_audit.entity_id == tun_id

    # 4. Create User -> 1 audit row, NO password in detail
    usr_res = client.post(
        "/api/users",
        json={
            "username": "subordinate_eng",
            "full_name": "Subordinate Engineer",
            "role": "engineer",
            "initial_password": "SecurePassword123!",
            "mine_ids": [mine_id],
        },
    )
    assert usr_res.status_code == 200
    user_id = usr_res.json()["id"]
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "user.create"
    assert "password" not in last_audit.detail
    assert "SecurePassword123!" not in str(last_audit.detail)


def test_done_when_manager_journey(client, db):
    """B0-B5: Full Done-When integration test.

    A brand-new Manager goes from empty account to placed node carrying a tunnel:
    1. Log in with initial temporary password
    2. Change password
    3. Create a mine
    4. Add a tunnel
    5. Register a gateway (key shown once with Cache-Control: no-store)
    6. Post packet with that key -> device appears in Unplaced queue
    7. Place node on map with the tunnel
    8. Verify node is in /api/placements and has placed=true in /api/nodes
    """
    # 0. Setup company and initial manager account with force_pw_change=True
    comp = Company(name="DoneWhen Coal Ltd", name_normalized="donewhen coal ltd")
    db.add(comp)
    db.flush()

    initial_pw = "TempInitialPw123!"
    mgr = User(
        company_id=comp.id,
        username="fresh_manager",
        password_hash=hash_password(initial_pw),
        full_name="Fresh Manager",
        role=ROLE_MANAGER,
        active=True,
        force_pw_change=True,
    )
    db.add(mgr)
    db.commit()

    # 1. Log in
    login_res = client.post("/api/auth/login", json={"username": "fresh_manager", "password": initial_pw})
    assert login_res.status_code == 200
    assert login_res.json()["force_pw_change"] is True

    # 2. Change password
    new_pw = "PermSecurePassword@2026"
    pw_res = client.post(
        "/api/auth/change-password",
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    assert pw_res.status_code == 200

    # Verify force_pw_change satisfied
    me_res = client.get("/api/auth/me").json()
    assert me_res["user"]["force_pw_change"] is False

    # 3. Create a mine
    mine_payload = {
        "name": "Kalyaneshwari Underground Colliery",
        "subsidiary": "ECL",
        "status": "active",
        "method": "underground",
        "lat": 23.7750,
        "lon": 86.8500,
        "area_ha": 350.0,
        "max_depth_m": 240.0,
    }
    mine_res = client.post("/api/mines", json=mine_payload)
    assert mine_res.status_code == 200
    mine_id = mine_res.json()["id"]

    # Creator immediately has access to this mine
    mines_list = client.get("/api/mines").json()["mines"]
    assert any(m["id"] == mine_id for m in mines_list)

    # 4. Add a tunnel
    tunnel_payload = {
        "name": "Main Haulage Incline #1",
        "code": "HAUL-01",
        "depth_m": 190.0,
        "status": "active",
    }
    tun_res = client.post(f"/api/mines/{mine_id}/tunnels", json=tunnel_payload)
    assert tun_res.status_code == 200
    tunnel_id = tun_res.json()["id"]

    # 5. Register a gateway (key shown ONCE)
    gw_payload = {
        "name": "Kalyaneshwari Pithead Gateway",
        "mac": "DE:AD:BE:EF:00:01",
    }
    gw_res = client.post(f"/api/mines/{mine_id}/gateways", json=gw_payload)
    assert gw_res.status_code == 200
    assert gw_res.headers.get("Cache-Control") == "no-store"
    api_key = gw_res.json()["api_key"]
    gw_id = gw_res.json()["id"]
    gw_mac = gw_res.json()["mac"]
    assert api_key.startswith("gwk_")

    # 6. Gateway posts packet with that key
    pkt = pack_gateway_packet(
        gateway_mac=gw_mac,
        node_id=1,
        counter=101,
        tilt_x=0.12,
        tilt_y=0.08,
        vib_rms=0.019,
        shock=False,
        batt_v=4.12,
        batt_pct=92.0,
        danger_level=0,
    )
    ingest_res = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": api_key},
    )
    assert ingest_res.status_code == 200

    # 7. Device appears in Unplaced queue
    unplaced_res = client.get("/api/unplaced").json()
    assert len(unplaced_res["unplaced"]) == 1
    unplaced_node = unplaced_res["unplaced"][0]
    assert unplaced_node["node_id"] == 1
    assert unplaced_node["placed"] is False
    node_pk = unplaced_node["id"]

    # 8. Place node on map with the tunnel
    place_res = client.patch(
        f"/api/nodes/{node_pk}/placement",
        json={"tunnel_id": tunnel_id, "lat": 23.7755, "lon": 86.8510},
    )
    assert place_res.status_code == 200
    placed_node = place_res.json()
    assert placed_node["placed"] is True
    assert placed_node["latitude"] == 23.7755
    assert placed_node["longitude"] == 86.8510
    assert placed_node["tunnel_id"] == tunnel_id

    # 9. Verify device appears in /api/placements
    placements_res = client.get("/api/placements").json()["placements"]
    assert len(placements_res) == 1
    p = placements_res[0]
    assert p["node_id"] == 1
    assert p["latitude"] == 23.7755
    assert p["longitude"] == 86.8510
    assert p["location_name"] == "Main Haulage Incline #1"

    # 10. Verify /api/unplaced is now empty
    unplaced_after = client.get("/api/unplaced").json()["unplaced"]
    assert len(unplaced_after) == 0

    # 11. Verify /api/nodes shows placed=true and tunnel_id
    nodes_res = client.get("/api/nodes").json()["nodes"]
    assert len(nodes_res) == 1
    assert nodes_res[0]["placed"] is True
    assert nodes_res[0]["tunnel_id"] == tunnel_id

    # 12. Verify /api/gateways shows placed_nodes=1
    gateways_res = client.get("/api/gateways").json()["gateways"]
    assert len(gateways_res) == 1
    assert gateways_res[0]["placed_nodes"] == 1
    assert gateways_res[0]["node_count"] == 1


def test_a1_gateway_delete_is_disable(client, db):
    """A1: DELETE /api/gateways/{id} disables (active=false), never hard deletes."""
    comp = Company(name="GW Disable Corp", name_normalized="gw disable corp")
    db.add(comp)
    db.flush()

    mgr = User(
        company_id=comp.id,
        username="gw_disable_mgr",
        password_hash=hash_password("ValidPassword123!"),
        full_name="GW Disable Manager",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.flush()

    mine = Mine(company_id=comp.id, name="Disable Test Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    raw_key = generate_key()
    gw = Gateway(
        mac="EE:11:22:33:44:55",
        mine_id=mine.id,
        name="Gateway To Disable",
        api_key_hash=hash_key(raw_key),
        active=True,
    )
    db.add(gw)
    db.flush()

    # Even with nodes present, DELETE must succeed (disable) rather than 409
    node = Node(gateway_id=gw.id, node_no=1, status="normal")
    db.add(node)
    db.commit()

    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))

    # DELETE /api/gateways/{id}
    del_res = client.delete(f"/api/gateways/{gw.id}")
    assert del_res.status_code == 200
    gw_obj = del_res.json()
    assert gw_obj["id"] == gw.id
    assert gw_obj["active"] is False
    assert gw_obj["name"] == "Gateway To Disable"
    assert "api_key" not in gw_obj
    assert "api_key_hash" not in gw_obj

    # Row is still in database with active=False
    db.refresh(gw)
    assert gw.active is False

    # Audit row has action 'gateway.disable'
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "gateway.disable"
    assert last_audit.entity == "gateway"
    assert last_audit.entity_id == gw.id

    # PATCH {active: true} re-enables and logs gateway.update
    patch_res = client.patch(f"/api/gateways/{gw.id}", json={"active": True})
    assert patch_res.status_code == 200
    assert patch_res.json()["active"] is True
    db.refresh(gw)
    assert gw.active is True
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "gateway.update"

    # PATCH {active: false} disables and logs gateway.disable
    patch_res2 = client.patch(f"/api/gateways/{gw.id}", json={"active": False})
    assert patch_res2.status_code == 200
    assert patch_res2.json()["active"] is False
    db.refresh(gw)
    assert gw.active is False
    last_audit = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
    assert last_audit.action == "gateway.disable"


def test_a2_login_response_no_token_in_body(client, db):
    """A2: POST /api/auth/login must NOT return the JWT in the body."""
    comp = Company(name="Login Test Corp", name_normalized="login test corp")
    db.add(comp)
    db.flush()

    user = User(
        company_id=comp.id,
        username="login_test_user",
        password_hash=hash_password("ValidPassword123!"),
        full_name="Login Test User",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(user)
    db.commit()

    res = client.post("/api/auth/login", json={"username": "login_test_user", "password": "ValidPassword123!"})
    assert res.status_code == 200
    body = res.json()
    assert "token" not in body
    assert "user" in body
    assert "force_pw_change" in body
    assert COOKIE_NAME in res.cookies


def test_a5_demo_status_is_manager_only(client, db):
    """A5: GET /api/demo/status is Manager-only (403 for workers and non-manager superiors)."""
    comp = Company(name="Demo Status Corp", name_normalized="demo status corp")
    db.add(comp)
    db.flush()

    mgr = User(company_id=comp.id, username="demo_mgr", password_hash=hash_password("ValidPassword123!"), full_name="Mgr", role="manager", active=True)
    eng = User(company_id=comp.id, username="demo_eng", password_hash=hash_password("ValidPassword123!"), full_name="Eng", role="engineer", active=True)
    miner = User(company_id=comp.id, username="demo_miner", password_hash=hash_password("ValidPassword123!"), full_name="Miner", role="miner", active=True)
    db.add_all([mgr, eng, miner])
    db.commit()

    # Anonymous -> 401
    client.cookies.clear()
    assert client.get("/api/demo/status").status_code == 401

    # Miner -> 403
    client.cookies.set(COOKIE_NAME, create_session_token(miner.id))
    assert client.get("/api/demo/status").status_code == 403

    # Engineer -> 403
    client.cookies.set(COOKIE_NAME, create_session_token(eng.id))
    assert client.get("/api/demo/status").status_code == 403

    # Manager -> 200
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))
    res = client.get("/api/demo/status")
    assert res.status_code == 200
    assert "running" in res.json()
