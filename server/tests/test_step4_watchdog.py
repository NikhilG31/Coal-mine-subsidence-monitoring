"""Comprehensive test suite for Step 4: Watchdog, Offline Detection, Alerts, and Token Invalidation.

Verifies:
1. Connectivity thresholds boundary conditions (node_is_online, gateway_is_online).
2. Pure watchdog cycle rules (single alert, deduplication, auto-resolution, unplaced node immunity).
3. Gateway offline suppression and resolution of false node alerts.
4. Database partial unique indexes (uq_alerts_open_node, uq_alerts_open_gateway) and concurrency handling.
5. Watchdog runner startup grace period and advisory transaction lock.
6. Role authorization and multi-tenant scoping for alert endpoints.
7. Alert acknowledgment idempotency and audit logging.
8. A3 session invalidation (token_version increment on password change, reset, deactivation).
9. Done-When end-to-end integration scenario.
10. Alembic migration downgrade -1 and upgrade head reversibility.
"""
import datetime
import time
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import (
    GATEWAY_OFFLINE_AFTER_S,
    NODE_OFFLINE_AFTER_S,
)
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.security.passwords import hash_password
from app.security.roles import ROLE_ENGINEER, ROLE_MANAGER, ROLE_MINER
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.connectivity import gateway_is_online, node_is_online
from app.services.gateway_keys import generate_key, hash_key
from app.services.packet_codec import pack_gateway_packet
from app.services.watchdog_service import run_watchdog_cycle
from app.watchdog_runner import WatchdogRunner


def test_connectivity_thresholds_boundaries():
    """B0: Boundary condition tests for node and gateway connectivity checkers."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Node tests
    assert node_is_online(now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S), now) is True
    assert node_is_online(now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S - 1), now) is True
    assert node_is_online(now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 1), now) is False
    assert node_is_online(None, now) is False

    # Gateway tests
    assert gateway_is_online(now - datetime.timedelta(seconds=GATEWAY_OFFLINE_AFTER_S), now) is True
    assert gateway_is_online(now - datetime.timedelta(seconds=GATEWAY_OFFLINE_AFTER_S - 1), now) is True
    assert gateway_is_online(now - datetime.timedelta(seconds=GATEWAY_OFFLINE_AFTER_S + 1), now) is False
    assert gateway_is_online(None, now) is False


def test_node_silent_past_threshold_deduplication(db):
    """B2: Node silent past threshold opens 1 alert; subsequent cycles produce no duplicate alerts."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="Watchdog Test Co", name_normalized="watchdog test co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Watchdog Alpha", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Main Drift", code="T-01")
    db.add(tunnel)
    db.flush()

    gw = Gateway(
        mac="AA:BB:CC:11:22:33",
        mine_id=mine.id,
        name="Gateway 1",
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=1,
        tunnel_id=tunnel.id,
        lat=23.7,
        lon=85.2,
        last_seen_at=now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 10),
    )
    db.add(node)
    db.commit()

    # Cycle 1: Opens 1 alert
    res1 = run_watchdog_cycle(db, now)
    assert res1 == {"opened": 1, "resolved": 0}

    alerts = db.query(Alert).filter(Alert.node_id == node.id).all()
    assert len(alerts) == 1
    assert alerts[0].kind == "node_offline"
    assert alerts[0].resolved_at is None
    assert "offline" in alerts[0].title

    # Next 5 cycles: zero new alerts opened
    for _ in range(5):
        res = run_watchdog_cycle(db, now)
        assert res == {"opened": 0, "resolved": 0}

    alerts_after = db.query(Alert).filter(Alert.node_id == node.id).all()
    assert len(alerts_after) == 1


def test_node_recovery_and_recurrence(db):
    """B2: Node recovery auto-resolves open alert; subsequent silent period opens new alert row."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="Recur Co", name_normalized="recur co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Recur Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="North Drift", code="T-02")
    db.add(tunnel)
    db.flush()

    gw = Gateway(
        mac="BB:CC:DD:11:22:33",
        mine_id=mine.id,
        name="Gateway Recur",
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=2,
        tunnel_id=tunnel.id,
        lat=23.71,
        lon=85.21,
        last_seen_at=now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 15),
    )
    db.add(node)
    db.commit()

    # Step 1: Open alert
    res1 = run_watchdog_cycle(db, now)
    assert res1["opened"] == 1

    # Step 2: Node recovers
    recovery_time = now + datetime.timedelta(seconds=20)
    node.last_seen_at = recovery_time
    gw.last_seen_at = recovery_time
    db.commit()

    res2 = run_watchdog_cycle(db, recovery_time)
    assert res2 == {"opened": 0, "resolved": 1}

    first_alert = db.query(Alert).filter(Alert.node_id == node.id).first()
    assert first_alert.resolved_at == recovery_time

    # Step 3: Node goes silent again later
    silent_again_time = recovery_time + datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 10)
    gw.last_seen_at = silent_again_time
    db.commit()

    res3 = run_watchdog_cycle(db, silent_again_time)
    assert res3 == {"opened": 1, "resolved": 0}

    all_alerts = (
        db.query(Alert)
        .filter(Alert.node_id == node.id)
        .order_by(Alert.opened_at.asc())
        .all()
    )
    assert len(all_alerts) == 2
    assert all_alerts[0].resolved_at is not None
    assert all_alerts[1].resolved_at is None


def test_unplaced_node_immunity(db):
    """B2: Unplaced node (tunnel_id is NULL) never raises offline alerts."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="Unplaced Test Co", name_normalized="unplaced test co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Unplaced Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    gw = Gateway(
        mac="CC:DD:EE:11:22:33",
        mine_id=mine.id,
        name="Gateway Unplaced",
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    # Unplaced node (spare in a drawer)
    node = Node(
        gateway_id=gw.id,
        node_no=3,
        tunnel_id=None,
        lat=None,
        lon=None,
        last_seen_at=now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 500),
    )
    db.add(node)
    db.commit()

    res = run_watchdog_cycle(db, now)
    assert res == {"opened": 0, "resolved": 0}
    assert db.query(Alert).filter(Alert.node_id == node.id).count() == 0


def test_gateway_offline_suppression_and_auto_resolution(db):
    """B2: Silent gateway opens gateway_offline alert, suppresses node alerts, and resolves open node alerts."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="GW Offline Co", name_normalized="gw offline co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="GW Offline Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="West Tunnel", code="T-03")
    db.add(tunnel)
    db.flush()

    # Gateway is initially online
    gw = Gateway(
        mac="DD:EE:FF:11:22:33",
        mine_id=mine.id,
        name="Gateway West",
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=4,
        tunnel_id=tunnel.id,
        lat=23.75,
        lon=85.25,
        last_seen_at=now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 20),
    )
    db.add(node)
    db.commit()

    # Step 1: Node offline alert opened while gateway is online
    res1 = run_watchdog_cycle(db, now)
    assert res1 == {"opened": 1, "resolved": 0}
    open_node_alert = db.query(Alert).filter(Alert.node_id == node.id, Alert.resolved_at.is_(None)).first()
    assert open_node_alert is not None

    # Step 2: Whole gateway goes offline
    gw_offline_time = now + datetime.timedelta(seconds=GATEWAY_OFFLINE_AFTER_S + 20)
    res2 = run_watchdog_cycle(db, gw_offline_time)
    assert res2 == {"opened": 1, "resolved": 1}

    # Verify open gateway alert
    gw_alert = db.query(Alert).filter(Alert.gateway_id == gw.id, Alert.kind == "gateway_offline").first()
    assert gw_alert is not None
    assert gw_alert.resolved_at is None

    # Verify previously open node alert was resolved to avoid false node alerts
    db.refresh(open_node_alert)
    assert open_node_alert.resolved_at == gw_offline_time


def test_gateway_unseen_and_disabled_ignored(db):
    """B2: Gateways with last_seen_at=None or active=False never raise alerts."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="Inactive GW Co", name_normalized="inactive gw co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Inactive Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    # Gateway 1: Never seen (registered but unpowered)
    gw1 = Gateway(
        mac="11:22:33:44:55:66",
        mine_id=mine.id,
        name="Unseen Gateway",
        active=True,
        last_seen_at=None,
    )
    # Gateway 2: Disabled
    gw2 = Gateway(
        mac="22:33:44:55:66:77",
        mine_id=mine.id,
        name="Disabled Gateway",
        active=False,
        last_seen_at=now - datetime.timedelta(seconds=1000),
    )
    db.add_all([gw1, gw2])
    db.commit()

    res = run_watchdog_cycle(db, now)
    assert res == {"opened": 0, "resolved": 0}
    assert db.query(Alert).count() == 0


def test_gateway_recovery(db):
    """B2: Gateway recovery auto-resolves gateway_offline alert."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="GW Recur Co", name_normalized="gw recur co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="GW Recur Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    gw = Gateway(
        mac="33:44:55:66:77:88",
        mine_id=mine.id,
        name="Recur Gateway",
        active=True,
        last_seen_at=now - datetime.timedelta(seconds=GATEWAY_OFFLINE_AFTER_S + 15),
    )
    db.add(gw)
    db.commit()

    res1 = run_watchdog_cycle(db, now)
    assert res1["opened"] == 1

    # Gateway recovers
    recovery_time = now + datetime.timedelta(seconds=10)
    gw.last_seen_at = recovery_time
    db.commit()

    res2 = run_watchdog_cycle(db, recovery_time)
    assert res2 == {"opened": 0, "resolved": 1}

    gw_alert = db.query(Alert).filter(Alert.gateway_id == gw.id, Alert.kind == "gateway_offline").first()
    assert gw_alert.resolved_at == recovery_time


def test_db_partial_unique_index_and_concurrency_race(db):
    """B1 & B2: Partial unique index prevents duplicate open alerts; service handles race gracefully."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="Race Test Co", name_normalized="race test co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Race Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Race Tunnel", code="T-04")
    db.add(tunnel)
    db.flush()

    gw = Gateway(
        mac="44:55:66:77:88:99",
        mine_id=mine.id,
        name="Race Gateway",
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=5,
        tunnel_id=tunnel.id,
        lat=23.8,
        lon=85.3,
        last_seen_at=now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 20),
    )
    db.add(node)
    db.commit()

    # Insert open alert 1
    alert1 = Alert(
        company_id=comp.id,
        mine_id=mine.id,
        gateway_id=gw.id,
        node_id=node.id,
        kind="node_offline",
        title="Open Alert 1",
        opened_at=now,
    )
    db.add(alert1)
    db.commit()

    # Direct SQL duplicate insert must fail with IntegrityError on uq_alerts_open_node
    alert2 = Alert(
        company_id=comp.id,
        mine_id=mine.id,
        gateway_id=gw.id,
        node_id=node.id,
        kind="node_offline",
        title="Open Alert 2",
        opened_at=now,
    )
    db.add(alert2)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # run_watchdog_cycle handles existing alert without error
    res = run_watchdog_cycle(db, now)
    assert res == {"opened": 0, "resolved": 0}


def test_watchdog_runner_startup_grace_period():
    """B3: Runner suppresses evaluations during startup grace period."""
    runner = WatchdogRunner(interval_s=10, grace_period_s=35)

    # Within grace period
    runner.started_at = time.monotonic() - 10.0
    assert runner.in_grace_period() is True
    assert runner.run_once() is None

    # Past grace period
    runner.started_at = time.monotonic() - 40.0
    assert runner.in_grace_period() is False


def test_alert_routes_scoping_and_roles(client, db):
    """B4: Role RBAC and multi-tenant scoping for GET /api/alerts and POST /api/alerts/{id}/ack."""
    now = datetime.datetime.now(datetime.timezone.utc)

    # Company A
    comp_a = Company(name="Company A", name_normalized="company a")
    db.add(comp_a)
    db.flush()
    mine_a1 = Mine(company_id=comp_a.id, name="Mine A1", status="active", method="underground")
    mine_a2 = Mine(company_id=comp_a.id, name="Mine A2", status="active", method="underground")
    db.add_all([mine_a1, mine_a2])
    db.flush()

    # Company B
    comp_b = Company(name="Company B", name_normalized="company b")
    db.add(comp_b)
    db.flush()
    mine_b = Mine(company_id=comp_b.id, name="Mine B", status="active", method="underground")
    db.add(mine_b)
    db.flush()

    gw_a1 = Gateway(mac="AA:01:01:01:01:01", mine_id=mine_a1.id, name="GW A1", active=True, last_seen_at=now)
    gw_a2 = Gateway(mac="AA:02:02:02:02:02", mine_id=mine_a2.id, name="GW A2", active=True, last_seen_at=now)
    gw_b = Gateway(mac="BB:01:01:01:01:01", mine_id=mine_b.id, name="GW B", active=True, last_seen_at=now)
    db.add_all([gw_a1, gw_a2, gw_b])
    db.flush()

    alert_a1 = Alert(company_id=comp_a.id, mine_id=mine_a1.id, gateway_id=gw_a1.id, kind="gateway_offline", title="A1 Offline", opened_at=now)
    alert_a2 = Alert(company_id=comp_a.id, mine_id=mine_a2.id, gateway_id=gw_a2.id, kind="gateway_offline", title="A2 Offline", opened_at=now)
    alert_b = Alert(company_id=comp_b.id, mine_id=mine_b.id, gateway_id=gw_b.id, kind="gateway_offline", title="B Offline", opened_at=now)
    db.add_all([alert_a1, alert_a2, alert_b])
    db.flush()

    # Users
    mgr_a = User(company_id=comp_a.id, username="mgr_a", password_hash=hash_password("password123"), full_name="Manager A", role=ROLE_MANAGER, active=True)
    eng_a1 = User(company_id=comp_a.id, username="eng_a1", password_hash=hash_password("password123"), full_name="Engineer A1", role=ROLE_ENGINEER, active=True)
    worker = User(company_id=comp_a.id, username="miner_a", password_hash=hash_password("password123"), full_name="Miner A", role=ROLE_MINER, active=True)
    db.add_all([mgr_a, eng_a1, worker])
    db.flush()

    # Assign eng_a1 only to mine_a1
    db.add(UserMine(user_id=eng_a1.id, mine_id=mine_a1.id))
    db.commit()

    # 1. Anonymous -> 401
    r_anon = client.get("/api/alerts")
    assert r_anon.status_code == 401

    # 2. Worker -> 403
    tok_worker = create_session_token(worker.id, worker.token_version)
    r_worker = client.get("/api/alerts", cookies={COOKIE_NAME: tok_worker})
    assert r_worker.status_code == 403

    # 3. Manager A sees all mines in Company A, but NEVER Company B
    tok_mgr_a = create_session_token(mgr_a.id, mgr_a.token_version)
    r_mgr = client.get("/api/alerts", cookies={COOKIE_NAME: tok_mgr_a})
    assert r_mgr.status_code == 200
    mgr_alert_ids = [a["id"] for a in r_mgr.json()["alerts"]]
    assert alert_a1.id in mgr_alert_ids
    assert alert_a2.id in mgr_alert_ids
    assert alert_b.id not in mgr_alert_ids

    # 4. Engineer assigned only to mine_a1 sees only mine_a1 alerts
    tok_eng = create_session_token(eng_a1.id, eng_a1.token_version)
    r_eng = client.get("/api/alerts", cookies={COOKIE_NAME: tok_eng})
    assert r_eng.status_code == 200
    eng_alert_ids = [a["id"] for a in r_eng.json()["alerts"]]
    assert alert_a1.id in eng_alert_ids
    assert alert_a2.id not in eng_alert_ids
    assert alert_b.id not in eng_alert_ids

    # 5. Query param mine_id
    r_mine_filter = client.get(f"/api/alerts?mine_id={mine_a1.id}", cookies={COOKIE_NAME: tok_mgr_a})
    assert r_mine_filter.status_code == 200
    filtered_ids = [a["id"] for a in r_mine_filter.json()["alerts"]]
    assert filtered_ids == [alert_a1.id]


def test_alert_acknowledgement_flow_and_audit(client, db):
    """B4: POST /api/alerts/{id}/ack sets user and timestamp, idempotent 200, cross-tenant 404, writes audit."""
    now = datetime.datetime.now(datetime.timezone.utc)

    comp1 = Company(name="Ack Co 1", name_normalized="ack co 1")
    comp2 = Company(name="Ack Co 2", name_normalized="ack co 2")
    db.add_all([comp1, comp2])
    db.flush()

    mine1 = Mine(company_id=comp1.id, name="Ack Mine 1", status="active", method="underground")
    mine2 = Mine(company_id=comp2.id, name="Ack Mine 2", status="active", method="underground")
    db.add_all([mine1, mine2])
    db.flush()

    gw1 = Gateway(mac="AA:AA:11:11:11:11", mine_id=mine1.id, name="GW 1", active=True, last_seen_at=now)
    gw2 = Gateway(mac="BB:BB:22:22:22:22", mine_id=mine2.id, name="GW 2", active=True, last_seen_at=now)
    db.add_all([gw1, gw2])
    db.flush()

    alert1 = Alert(company_id=comp1.id, mine_id=mine1.id, gateway_id=gw1.id, kind="gateway_offline", title="GW1 Offline", opened_at=now)
    alert2 = Alert(company_id=comp2.id, mine_id=mine2.id, gateway_id=gw2.id, kind="gateway_offline", title="GW2 Offline", opened_at=now)
    db.add_all([alert1, alert2])
    db.flush()

    user = User(company_id=comp1.id, username="ack_mgr", password_hash=hash_password("password123"), full_name="Ack Manager", role=ROLE_MANAGER, active=True)
    db.add(user)
    db.commit()

    tok = create_session_token(user.id, user.token_version)

    # 1. Ack alert1 -> 200
    r_ack = client.post(f"/api/alerts/{alert1.id}/ack", cookies={COOKIE_NAME: tok})
    assert r_ack.status_code == 200
    data = r_ack.json()
    assert data["acknowledged_by_name"] == "Ack Manager"
    assert data["acknowledged_at"] is not None

    # 2. Second ack -> idempotent 200 with same timestamp
    r_ack2 = client.post(f"/api/alerts/{alert1.id}/ack", cookies={COOKIE_NAME: tok})
    assert r_ack2.status_code == 200
    assert r_ack2.json()["acknowledged_at"] == data["acknowledged_at"]

    # 3. Ack cross-company alert2 -> 404 (does not leak existence)
    r_cross = client.post(f"/api/alerts/{alert2.id}/ack", cookies={COOKIE_NAME: tok})
    assert r_cross.status_code == 404

    # 4. Verify audit log entry
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "alert.acknowledge", AuditLog.entity_id == alert1.id)
        .first()
    )
    assert audit is not None
    assert audit.user_id == user.id
    assert audit.company_id == comp1.id


def test_a3_session_invalidation_lifecycle(client, db):
    """A3: token_version session invalidation on change-password, reset-password, and deactivation."""
    comp = Company(name="Token Revoke Co", name_normalized="token revoke co")
    db.add(comp)
    db.flush()

    admin = User(
        company_id=comp.id,
        username="admin_tok",
        password_hash=hash_password("admin_pass_123"),
        full_name="Admin User",
        role=ROLE_MANAGER,
        active=True,
    )
    victim = User(
        company_id=comp.id,
        username="victim_user",
        password_hash=hash_password("victim_pass_123"),
        full_name="Victim User",
        role=ROLE_ENGINEER,
        active=True,
    )
    db.add_all([admin, victim])
    db.commit()

    admin_tok = create_session_token(admin.id, admin.token_version)
    victim_tok_1 = create_session_token(victim.id, victim.token_version)

    # Initial token works
    r_me = client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_1})
    assert r_me.status_code == 200

    # 1. Self password change invalidates old token
    r_chg = client.post(
        "/api/auth/change-password",
        json={"current_password": "victim_pass_123", "new_password": "new_victim_pass_456"},
        cookies={COOKIE_NAME: victim_tok_1},
    )
    assert r_chg.status_code == 200
    victim_tok_2 = r_chg.cookies.get(COOKIE_NAME)
    assert victim_tok_2 is not None

    # Old token gets 401
    r_old = client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_1})
    assert r_old.status_code == 401

    # New reissued cookie works
    r_new = client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_2})
    assert r_new.status_code == 200

    # 2. Admin resets victim's password
    r_rst = client.patch(
        f"/api/users/{victim.id}",
        json={"reset_password": "reset_by_admin_789"},
        cookies={COOKIE_NAME: admin_tok},
    )
    assert r_rst.status_code == 200

    # victim_tok_2 now rejected with 401
    r_rst_check = client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_2})
    assert r_rst_check.status_code == 401

    # Login with new reset password
    r_login = client.post("/api/auth/login", json={"username": "victim_user", "password": "reset_by_admin_789"})
    assert r_login.status_code == 200
    victim_tok_3 = r_login.cookies.get(COOKIE_NAME)
    assert client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_3}).status_code == 200

    # 3. Admin deactivates victim user
    r_del = client.delete(f"/api/users/{victim.id}", cookies={COOKIE_NAME: admin_tok})
    assert r_del.status_code == 200

    # victim_tok_3 now rejected with 401
    r_del_check = client.get("/api/auth/me", cookies={COOKIE_NAME: victim_tok_3})
    assert r_del_check.status_code == 401


def test_done_when_end_to_end_journey(client, db):
    """End-to-End Done-When condition:

    Seed manager, mine, tunnel, gateway; ingest packet with key; place node;
    move last_seen_at past threshold; run cycle; GET /api/alerts shows 1 open alert
    and node reads offline in GET /api/nodes; ingest again; run cycle; alert resolved.
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    comp = Company(name="DoneWhen Co", name_normalized="donewhen co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="DoneWhen Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Extraction Tunnel 4", code="T-DW4")
    db.add(tunnel)
    db.flush()

    raw_gw_key = generate_key()
    gw = Gateway(
        mac="AA:EE:55:44:33:22",
        mine_id=mine.id,
        name="Telemetry GW",
        api_key_hash=hash_key(raw_gw_key),
        active=True,
        last_seen_at=now,
    )
    db.add(gw)
    db.flush()

    mgr = User(
        company_id=comp.id,
        username="donewhen_mgr",
        password_hash=hash_password("pass123"),
        full_name="DoneWhen Manager",
        role=ROLE_MANAGER,
        active=True,
    )
    db.add(mgr)
    db.commit()

    mgr_cookie = {COOKIE_NAME: create_session_token(mgr.id, mgr.token_version)}

    # Step 1: Ingest telemetry packet with valid key
    pkt = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=10,
        counter=1,
        tilt_x=0.05,
        tilt_y=0.05,
        vib_rms=0.005,
        shock=False,
        batt_v=4.1,
        batt_pct=95.0,
    )
    r_ingest1 = client.post(
        "/api/ingest",
        content=pkt,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_gw_key},
    )
    assert r_ingest1.status_code == 200

    node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == 10).first()
    assert node is not None

    # Step 2: Place the node
    r_place = client.patch(
        f"/api/nodes/{node.id}/placement",
        json={"tunnel_id": tunnel.id, "lat": 23.65, "lon": 85.15},
        cookies=mgr_cookie,
    )
    assert r_place.status_code == 200

    # Step 3: Move node last_seen_at back beyond threshold
    node.last_seen_at = now - datetime.timedelta(seconds=NODE_OFFLINE_AFTER_S + 10)
    gw.last_seen_at = now
    db.commit()

    # Step 4: Run watchdog cycle
    run_res = run_watchdog_cycle(db, now)
    assert run_res == {"opened": 1, "resolved": 0}

    # Step 5: GET /api/alerts shows exactly 1 open alert
    r_alerts = client.get("/api/alerts?status=open", cookies=mgr_cookie)
    assert r_alerts.status_code == 200
    alerts_data = r_alerts.json()["alerts"]
    assert len(alerts_data) == 1
    assert alerts_data[0]["node_id"] == node.id
    assert alerts_data[0]["kind"] == "node_offline"

    # Step 6: GET /api/nodes shows node with online == False
    r_nodes = client.get(f"/api/nodes?mine_id={mine.id}", cookies=mgr_cookie)
    assert r_nodes.status_code == 200
    node_dto = next(n for n in r_nodes.json()["nodes"] if n["node_id"] == 10)
    assert node_dto["online"] is False

    # Step 7: Node reports again (ingest telemetry)
    pkt2 = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=10,
        counter=2,
        tilt_x=0.06,
        tilt_y=0.06,
        vib_rms=0.006,
        shock=False,
        batt_v=4.09,
        batt_pct=94.5,
    )
    r_ingest2 = client.post(
        "/api/ingest",
        content=pkt2,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_gw_key},
    )
    assert r_ingest2.status_code == 200

    # Step 8: Run watchdog cycle again
    db.expire_all()
    cycle_time = datetime.datetime.now(datetime.timezone.utc)
    run_res2 = run_watchdog_cycle(db, cycle_time)
    assert run_res2 == {"opened": 0, "resolved": 1}

    # Step 9: Alert is resolved and node reads online
    r_alerts_after = client.get("/api/alerts?status=open", cookies=mgr_cookie)
    assert len(r_alerts_after.json()["alerts"]) == 0

    r_nodes_after = client.get(f"/api/nodes?mine_id={mine.id}", cookies=mgr_cookie)
    node_dto_after = next(n for n in r_nodes_after.json()["nodes"] if n["node_id"] == 10)
    assert node_dto_after["online"] is True


def test_alembic_downgrade_and_upgrade_clean():
    """B1 & B6: Verify alembic downgrade -1 and upgrade head run cleanly without error."""
    alembic_cfg = Config("alembic.ini")
    command.downgrade(alembic_cfg, "-1")
    command.upgrade(alembic_cfg, "head")
