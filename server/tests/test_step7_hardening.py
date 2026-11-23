"""Tests for Step 7.5 Hardening (Phase A fixes).

Verifies gateway-based node scoping, siren reliability, roll-call cross-tunnel
accounting, auto-incident ingest matrix, command status, and ingest security.
Must NOT touch firmware files, mock datetime module, or run against dev database.
"""
import datetime
import hashlib
from typing import Any, Dict
import pytest
from sqlalchemy.orm import Session

from app.config import (
    COMMAND_RETENTION_DAYS,
    COMMAND_TTL_S,
    DEMO_GATEWAY_KEY,
    SIREN_REFRESH_S,
)
from app.database import SessionLocal
from app.models.audit_log import AuditLog
from app.models.check_event import CheckEvent
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.incident_roster import IncidentRoster
from app.models.mine import Mine
from app.models.node import Node
from app.models.node_command import NodeCommand
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.security.passwords import hash_password
from app.security.roles import ROLE_FOREMAN, ROLE_MANAGER, ROLE_MINER, ROLE_SAFETY_OFFICER
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.badge_service import issue_badge
from app.services.checkin_service import record_scan
from app.services.incident_service import (
    auto_open_for_critical,
    close_incident,
    open_incident,
    set_siren,
)
from app.services.node_command_service import create_command
from app.services.packet_codec import pack_gateway_packet
from app.services.roll_call_service import mark_accounted_on_exit
from app.services.siren_service import run_siren_cycle
from app.services.watchdog_service import run_watchdog_cycle
from app.watchdog_runner import WatchdogRunner


def _login(client, user_id: int):
    client.cookies.set(COOKIE_NAME, create_session_token(user_id))


def _setup_hardening_env(db: Session) -> Dict[str, Any]:
    # Company A
    comp_a = Company(name="Hardening Corp A", name_normalized="hardening corp a")
    db.add(comp_a)
    db.flush()

    mine_a = Mine(company_id=comp_a.id, name="Mine Alpha", status="active", method="underground")
    db.add(mine_a)
    db.flush()

    tun_a1 = Tunnel(mine_id=mine_a.id, name="Tunnel A1", code="T-A1")
    tun_a2 = Tunnel(mine_id=mine_a.id, name="Tunnel A2", code="T-A2")
    db.add_all([tun_a1, tun_a2])
    db.flush()

    raw_key_a = DEMO_GATEWAY_KEY or "hardening_gw_key_for_test_a0001"
    gw_hash_a = hashlib.sha256(raw_key_a.encode("utf-8")).hexdigest()
    gw_a = Gateway(
        mine_id=mine_a.id,
        name="Gateway Alpha",
        mac="AA:AA:AA:11:22:33",
        api_key_hash=gw_hash_a,
        active=True,
    )
    db.add(gw_a)
    db.flush()

    node_placed = Node(
        gateway_id=gw_a.id,
        node_no=1,
        tunnel_id=tun_a1.id,
        lat=23.750,
        lon=86.420,
        status="normal",
        battery_pct=90,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    node_unplaced = Node(
        gateway_id=gw_a.id,
        node_no=2,
        tunnel_id=None,
        lat=None,
        lon=None,
        status="normal",
        battery_pct=85,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    db.add_all([node_placed, node_unplaced])
    db.flush()

    # Company B (Cross-company)
    comp_b = Company(name="Hardening Corp B", name_normalized="hardening corp b")
    db.add(comp_b)
    db.flush()

    mine_b = Mine(company_id=comp_b.id, name="Mine Beta", status="active", method="underground")
    db.add(mine_b)
    db.flush()

    tun_b1 = Tunnel(mine_id=mine_b.id, name="Tunnel B1", code="T-B1")
    db.add(tun_b1)
    db.flush()

    raw_key_b = "hardening_gw_key_for_test_b0002"
    gw_hash_b = hashlib.sha256(raw_key_b.encode("utf-8")).hexdigest()
    gw_b = Gateway(
        mine_id=mine_b.id,
        name="Gateway Beta",
        mac="BB:BB:BB:11:22:33",
        api_key_hash=gw_hash_b,
        active=True,
    )
    db.add(gw_b)
    db.flush()

    node_b = Node(
        gateway_id=gw_b.id,
        node_no=99,
        tunnel_id=tun_b1.id,
        lat=23.800,
        lon=86.500,
        status="normal",
        battery_pct=92,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    db.add(node_b)
    db.flush()

    # Users
    mgr_a = User(company_id=comp_a.id, username="h_mgr_a", full_name="Manager A", role=ROLE_MANAGER, password_hash=hash_password("Pass@12345"), active=True)
    foreman_a = User(company_id=comp_a.id, username="h_foreman_a", full_name="Foreman A", role=ROLE_FOREMAN, password_hash=hash_password("Pass@12345"), active=True)
    miner_a = User(company_id=comp_a.id, username="h_miner_a", full_name="Miner A", role=ROLE_MINER, password_hash=hash_password("Pass@12345"), active=True)
    mgr_b = User(company_id=comp_b.id, username="h_mgr_b", full_name="Manager B", role=ROLE_MANAGER, password_hash=hash_password("Pass@12345"), active=True)
    db.add_all([mgr_a, foreman_a, miner_a, mgr_b])
    db.flush()

    db.add(UserMine(user_id=mgr_a.id, mine_id=mine_a.id))
    db.add(UserMine(user_id=foreman_a.id, mine_id=mine_a.id))
    db.add(UserMine(user_id=miner_a.id, mine_id=mine_a.id))
    db.add(UserMine(user_id=mgr_b.id, mine_id=mine_b.id))

    db.add(WorkerProfile(company_id=comp_a.id, user_id=miner_a.id, employee_no="EMP-HA1"))
    db.commit()
    badge_token, _, _ = issue_badge(db, mgr_a, miner_a.id)

    return {
        "comp_a": comp_a,
        "mine_a": mine_a,
        "tun_a1": tun_a1,
        "tun_a2": tun_a2,
        "gw_a": gw_a,
        "raw_key_a": raw_key_a,
        "node_placed": node_placed,
        "node_unplaced": node_unplaced,
        "mgr_a": mgr_a,
        "foreman_a": foreman_a,
        "miner_a": miner_a,
        "badge_token": badge_token,
        "comp_b": comp_b,
        "mine_b": mine_b,
        "gw_b": gw_b,
        "raw_key_b": raw_key_b,
        "node_b": node_b,
        "mgr_b": mgr_b,
    }


def test_cross_company_node_commands_scoping(client, db):
    """A1, A8: Cross-company commands return 404 identical to not found for placed and unplaced."""
    env = _setup_hardening_env(db)
    mgr_b = env["mgr_b"]
    node_placed = env["node_placed"]
    node_unplaced = env["node_unplaced"]

    _login(client, mgr_b.id)

    # Placed node cross-company POST -> 404
    r_post_p = client.post(f"/api/nodes/{node_placed.id}/commands", json={"cmd": "buzzer_on"})
    assert r_post_p.status_code == 404
    assert r_post_p.json()["detail"] == "Node not found"

    # Unplaced node cross-company POST -> 404
    r_post_u = client.post(f"/api/nodes/{node_unplaced.id}/commands", json={"cmd": "buzzer_on"})
    assert r_post_u.status_code == 404
    assert r_post_u.json()["detail"] == "Node not found"

    # Placed node cross-company GET -> 404
    r_get_p = client.get(f"/api/nodes/{node_placed.id}/commands")
    assert r_get_p.status_code == 404
    assert r_get_p.json()["detail"] == "Node not found"

    # Unplaced node cross-company GET -> 404
    r_get_u = client.get(f"/api/nodes/{node_unplaced.id}/commands")
    assert r_get_u.status_code == 404
    assert r_get_u.json()["detail"] == "Node not found"


def test_unplaced_node_command_dispatch(client, db):
    """A1, A8: Unplaced node rejects command dispatch with 404, but permits scoped GET."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node_unplaced = env["node_unplaced"]

    _login(client, mgr_a.id)

    # POST on unplaced node -> 404 Node not found
    r_post = client.post(f"/api/nodes/{node_unplaced.id}/commands", json={"cmd": "buzzer_on"})
    assert r_post.status_code == 404
    assert r_post.json()["detail"] == "Node not found"

    # GET on unplaced node -> 200 []
    r_get = client.get(f"/api/nodes/{node_unplaced.id}/commands")
    assert r_get.status_code == 200
    assert r_get.json()["commands"] == []


def test_inactive_gateway_command_dispatch(client, db):
    """A1, A8: Inactive gateway node rejects command dispatch with 404, permits scoped GET."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node_placed = env["node_placed"]
    gw_a = env["gw_a"]

    gw_a.active = False
    db.commit()

    _login(client, mgr_a.id)

    # POST with inactive gateway -> 404
    r_post = client.post(f"/api/nodes/{node_placed.id}/commands", json={"cmd": "buzzer_on"})
    assert r_post.status_code == 404
    assert r_post.json()["detail"] == "Node not found"

    # GET with inactive gateway -> 200
    r_get = client.get(f"/api/nodes/{node_placed.id}/commands")
    assert r_get.status_code == 200


def test_siren_cycle_runner_persistence_and_close(db, freeze_clock):
    """A2, A8: run_siren_once persists commands via runner session; close creates buzzer_off and stops cycle."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node = env["node_placed"]

    t0 = datetime.datetime(2030, 9, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    inc = open_incident(db, mgr_a, env["mine_a"].id, env["tun_a1"].id, "Siren Runner Test", level=3, siren=True, now=t0)
    db.commit()

    # Initial commands exist
    cmds = db.query(NodeCommand).filter(NodeCommand.node_id == node.id).all()
    assert len(cmds) == 1
    assert cmds[0].cmd == "buzzer_on"

    # Mark acked at t0
    cmds[0].acked_at = t0
    db.commit()

    # Fast forward past SIREN_REFRESH_S (30s)
    t1 = t0 + datetime.timedelta(seconds=35)
    freeze_clock(t1)

    # Runner executes run_siren_once with real session
    runner = WatchdogRunner()
    refreshed = runner.run_siren_once(now=t1)
    assert refreshed == 1

    # Verify persisted in database
    with SessionLocal() as verify_db:
        cmds_after = verify_db.query(NodeCommand).filter(NodeCommand.node_id == node.id).all()
        assert len(cmds_after) == 2
        assert cmds_after[-1].seq == 2

    # Close incident
    t2 = t1 + datetime.timedelta(seconds=5)
    freeze_clock(t2)
    close_incident(db, actor=mgr_a, incident=inc, close_note="Incident cleared", confirm_unaccounted=True, now=t2)
    db.commit()

    # BUZZER_OFF command exists
    off_cmds = db.query(NodeCommand).filter(NodeCommand.node_id == node.id, NodeCommand.cmd == "buzzer_off").all()
    assert len(off_cmds) == 1

    # Subsequent siren cycle produces 0 commands
    t3 = t2 + datetime.timedelta(seconds=40)
    freeze_clock(t3)
    assert run_siren_cycle(db, now=t3) == 0


def test_manual_per_node_buzzer_off_not_overridden_by_siren_cycle(db, freeze_clock):
    """A2, A8: Manual per-node buzzer_off is not overridden by siren cycle until incident siren toggled."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node = env["node_placed"]

    t0 = datetime.datetime(2030, 9, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    inc = open_incident(db, mgr_a, env["mine_a"].id, env["tun_a1"].id, "Manual Off Test", level=3, siren=True, now=t0)
    db.commit()

    # Ack initial command
    initial_cmd = db.query(NodeCommand).filter(NodeCommand.node_id == node.id).first()
    initial_cmd.acked_at = t0
    db.commit()

    # Superior manually silences node at t0 + 10s
    t_off = t0 + datetime.timedelta(seconds=10)
    freeze_clock(t_off)
    manual_off = create_command(db, actor=mgr_a, node=node, cmd="buzzer_off", now=t_off)
    manual_off.acked_at = t_off
    db.commit()

    # Fast forward past SIREN_REFRESH_S: cycle runs at t0 + 40s
    t_cycle = t0 + datetime.timedelta(seconds=40)
    freeze_clock(t_cycle)
    count = run_siren_cycle(db, now=t_cycle)
    assert count == 0  # Node is skipped because buzzer_off > last siren-on event

    # Incident-level siren-on re-enables it
    t_re_on = t0 + datetime.timedelta(seconds=50)
    freeze_clock(t_re_on)
    set_siren(db, actor=mgr_a, incident=inc, on=True, now=t_re_on)
    db.commit()

    # Verify new buzzer_on was issued
    latest_cmd = db.query(NodeCommand).filter(NodeCommand.node_id == node.id).order_by(NodeCommand.id.desc()).first()
    assert latest_cmd.cmd == "buzzer_on"


def test_pending_siren_command_extended_not_duplicated(db, freeze_clock):
    """A2, A8: Pending unacked buzzer_on has expires_at extended; no duplicate row created."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node = env["node_placed"]

    t0 = datetime.datetime(2030, 9, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    open_incident(db, mgr_a, env["mine_a"].id, env["tun_a1"].id, "Pending Extend Test", level=3, siren=True, now=t0)
    db.commit()

    cmd = db.query(NodeCommand).filter(NodeCommand.node_id == node.id).first()
    original_exp = cmd.expires_at

    # At T0 + 10s: still unacked, run siren cycle
    t1 = t0 + datetime.timedelta(seconds=10)
    freeze_clock(t1)
    refreshed = run_siren_cycle(db, now=t1)
    db.commit()

    assert refreshed == 1
    db.refresh(cmd)
    assert cmd.expires_at > original_exp
    assert cmd.expires_at == t1 + datetime.timedelta(seconds=COMMAND_TTL_S)

    # Row count is still exactly 1
    all_cmds = db.query(NodeCommand).filter(NodeCommand.node_id == node.id).all()
    assert len(all_cmds) == 1


def test_watchdog_retention_deletes_old_commands(db, freeze_clock):
    """A2, A8: Watchdog deletes commands acked or expired older than COMMAND_RETENTION_DAYS."""
    env = _setup_hardening_env(db)
    node = env["node_placed"]

    t_now = datetime.datetime(2030, 10, 1, 12, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t_now)

    retention_cutoff = t_now - datetime.timedelta(days=COMMAND_RETENTION_DAYS)

    # 1. Old acked command (older than retention cutoff) -> to be deleted
    c_old_acked = NodeCommand(
        node_id=node.id,
        cmd="buzzer_on",
        seq=1,
        created_at=retention_cutoff - datetime.timedelta(days=5),
        expires_at=retention_cutoff - datetime.timedelta(days=4),
        acked_at=retention_cutoff - datetime.timedelta(days=4),
    )
    # 2. Old expired command (unacked, older than retention cutoff) -> to be deleted
    c_old_expired = NodeCommand(
        node_id=node.id,
        cmd="buzzer_on",
        seq=2,
        created_at=retention_cutoff - datetime.timedelta(days=5),
        expires_at=retention_cutoff - datetime.timedelta(days=4),
        acked_at=None,
    )
    # 3. Recent acked command (kept)
    c_recent_acked = NodeCommand(
        node_id=node.id,
        cmd="buzzer_on",
        seq=3,
        created_at=t_now - datetime.timedelta(days=5),
        expires_at=t_now - datetime.timedelta(days=4),
        acked_at=t_now - datetime.timedelta(days=4),
    )
    # 4. Active pending command (kept)
    c_pending = NodeCommand(
        node_id=node.id,
        cmd="buzzer_on",
        seq=4,
        created_at=t_now,
        expires_at=t_now + datetime.timedelta(seconds=60),
        acked_at=None,
    )
    db.add_all([c_old_acked, c_old_expired, c_recent_acked, c_pending])
    db.commit()

    id_old_acked = c_old_acked.id
    id_old_expired = c_old_expired.id
    id_recent_acked = c_recent_acked.id
    id_pending = c_pending.id

    run_watchdog_cycle(db, now=t_now)
    db.commit()

    remaining_ids = {c.id for c in db.query(NodeCommand).all()}
    assert id_old_acked not in remaining_ids
    assert id_old_expired not in remaining_ids
    assert id_recent_acked in remaining_ids
    assert id_pending in remaining_ids


def test_cross_tunnel_scan_out_accounts_worker(db, freeze_clock):
    """A3: Out scan in tunnel B accounts worker in tunnel A open incident without audit row."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    miner_a = env["miner_a"]
    badge_token = env["badge_token"]
    tun_a1 = env["tun_a1"]
    tun_a2 = env["tun_a2"]

    t0 = datetime.datetime(2030, 9, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Worker checks in to Tunnel A1
    record_scan(db, scanner=mgr_a, token=badge_token, direction="in", tunnel_id=tun_a1.id, now=t0)
    db.commit()

    # Open incident in Tunnel A1
    t_inc = t0 + datetime.timedelta(minutes=10)
    freeze_clock(t_inc)
    inc = open_incident(db, mgr_a, env["mine_a"].id, tun_a1.id, "Emergency A1", level=3, siren=False, now=t_inc)
    db.commit()

    # Verify miner_a is on roster and unaccounted
    roster_item = (
        db.query(IncidentRoster)
        .filter(IncidentRoster.incident_id == inc.id, IncidentRoster.user_id == miner_a.id)
        .first()
    )
    assert roster_item is not None
    assert roster_item.accounted_at is None

    # Count audit rows before scan out
    audits_before = (
        db.query(AuditLog)
        .filter(AuditLog.entity == "incident", AuditLog.action == "incident.account")
        .count()
    )

    # Worker scans OUT at Tunnel A2
    t_out = t_inc + datetime.timedelta(minutes=5)
    freeze_clock(t_out)
    record_scan(db, scanner=mgr_a, token=badge_token, direction="out", tunnel_id=tun_a2.id, now=t_out)
    db.commit()

    # Worker is now accounted in Tunnel A1's incident
    db.refresh(roster_item)
    assert roster_item.accounted_at == t_out
    assert roster_item.accounted_by == mgr_a.id

    # Verify no incident.account audit row written
    audits_after = (
        db.query(AuditLog)
        .filter(AuditLog.entity == "incident", AuditLog.action == "incident.account")
        .count()
    )
    assert audits_after == audits_before


def test_auto_incident_ingest_matrix(client, db, monkeypatch):
    """A4: Ingest 0->3 on placed node opens auto incident; 2nd critical skips; unplaced skips; failure stores reading."""
    env = _setup_hardening_env(db)
    gw_a = env["gw_a"]
    raw_key = env["raw_key_a"]
    node_p = env["node_placed"]
    node_u = env["node_unplaced"]

    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": raw_key}

    # 1. Level 0 -> 2 (warning): opens NO incident
    pkt_lvl2 = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=1,
        tilt_x=1.2,
        tilt_y=0.0,
        vib_rms=0.02,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
    )
    r2 = client.post("/api/ingest", content=pkt_lvl2, headers=headers)
    assert r2.status_code == 200
    assert db.query(Incident).count() == 0

    # 2. Level 2 -> 3 (critical): opens EXACTLY ONE auto incident with siren
    pkt_lvl3 = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=2,
        tilt_x=3.5,
        tilt_y=0.0,
        vib_rms=0.15,
        shock=True,
        batt_v=4.0,
        batt_pct=90.0,
    )
    r3 = client.post("/api/ingest", content=pkt_lvl3, headers=headers)
    assert r3.status_code == 200
    incidents = db.query(Incident).all()
    assert len(incidents) == 1
    assert incidents[0].source == "auto"
    assert incidents[0].level == 3
    assert incidents[0].siren_active is True

    # 3. Second critical packet on same node: does NOT duplicate incident
    pkt_lvl3_repeat = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=3,
        tilt_x=13.0,
        tilt_y=0.0,
        vib_rms=0.18,
        shock=True,
        batt_v=4.0,
        batt_pct=89.0,
    )
    r3_rep = client.post("/api/ingest", content=pkt_lvl3_repeat, headers=headers)
    assert r3_rep.status_code == 200
    assert db.query(Incident).count() == 1

    # 4. Critical packet on unplaced node: opens NO incident
    pkt_unplaced = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_u.node_no,
        counter=1,
        tilt_x=15.0,
        tilt_y=0.0,
        vib_rms=0.20,
        shock=True,
        batt_v=4.0,
        batt_pct=85.0,
    )
    r_unp = client.post("/api/ingest", content=pkt_unplaced, headers=headers)
    assert r_unp.status_code == 200
    assert db.query(Incident).count() == 1

    # 5. Injected failure in incident service does NOT fail 200 response and stores reading
    def _faulty_auto_open(*args, **kwargs):
        raise RuntimeError("Injected incident opening failure")

    monkeypatch.setattr("app.services.incident_service.auto_open_for_critical", _faulty_auto_open)
    pkt_fault = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=4,
        tilt_x=14.0,
        tilt_y=0.0,
        vib_rms=0.19,
        shock=True,
        batt_v=4.0,
        batt_pct=88.0,
    )
    r_fault = client.post("/api/ingest", content=pkt_fault, headers=headers)
    assert r_fault.status_code == 200
    # Reading was stored despite incident failure
    latest_reading = db.query(Reading).filter(Reading.node_id == node_p.id, Reading.counter == 4).first()
    assert latest_reading is not None


def test_post_account_on_closed_incident_returns_409(client, db):
    """A6: Accounting worker on closed incident returns 409 {"code": "incident_closed"}."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    miner_a = env["miner_a"]

    inc = open_incident(db, mgr_a, env["mine_a"].id, env["tun_a1"].id, "Closed Incident Test", level=2, siren=False)
    db.commit()

    close_incident(db, mgr_a, inc, close_note="Incident closed", confirm_unaccounted=True)
    db.commit()

    _login(client, mgr_a.id)
    r = client.post(f"/api/incidents/{inc.id}/roster/{miner_a.id}/account")
    assert r.status_code == 409
    data = r.json()
    assert data["code"] == "incident_closed"
    assert "closed" in data["detail"].lower()


def test_forwarded_proto_and_unkeyed_ingest_and_gateway_isolation(client, db, monkeypatch):
    """A5, A8: X-Forwarded-Proto, unkeyed empty response, gateway command isolation, invalid seq ack."""
    env = _setup_hardening_env(db)
    gw_a = env["gw_a"]
    gw_b = env["gw_b"]
    node_p = env["node_placed"]
    node_b = env["node_b"]

    # Queue commands for Gateway A and Gateway B
    cmd_a = create_command(db, actor=None, node=node_p, cmd="buzzer_on")
    cmd_b = create_command(db, actor=None, node=node_b, cmd="buzzer_on")
    db.commit()

    pkt_a = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=10,
        tilt_x=0.0,
        tilt_y=0.0,
        vib_rms=0.0,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
    )

    # 1. Unkeyed ingest with ALLOW_UNKEYED_INGEST=True returns EMPTY body
    monkeypatch.setattr("app.routers.ingest.ALLOW_UNKEYED_INGEST", True)
    r_unkeyed = client.post(
        "/api/ingest",
        content=pkt_a,
        headers={"Content-Type": "application/octet-stream", "X-Forwarded-Proto": "https"},
    )
    assert r_unkeyed.status_code == 200
    assert r_unkeyed.text == ""  # Never returns command lines

    # 2. TRUST_FORWARDED_PROTO=False ignores X-Forwarded-Proto
    monkeypatch.setattr("app.routers.ingest.TRUST_FORWARDED_PROTO", False)
    monkeypatch.setattr("app.routers.ingest.ALLOW_UNKEYED_INGEST", False)
    r_ignore_proto = client.post(
        "/api/ingest",
        content=pkt_a,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Gateway-Key": env["raw_key_a"],
            "X-Forwarded-Proto": "https",
        },
    )
    assert r_ignore_proto.status_code == 200
    # Command withheld because client scheme is http (not trusted from header)
    assert r_ignore_proto.text == ""

    # 3. TRUST_FORWARDED_PROTO=True trusts X-Forwarded-Proto
    monkeypatch.setattr("app.routers.ingest.TRUST_FORWARDED_PROTO", True)
    r_trust_proto = client.post(
        "/api/ingest",
        content=pkt_a,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Gateway-Key": env["raw_key_a"],
            "X-Forwarded-Proto": "https",
        },
    )
    assert r_trust_proto.status_code == 200
    lines = r_trust_proto.text.strip().splitlines()
    assert len(lines) == 1
    assert lines[0] == f"CMD,{node_p.node_no},BUZZER_ON,{cmd_a.seq}"

    # Verify Gateway A NEVER receives Gateway B's commands
    assert f"CMD,{node_b.node_no}" not in r_trust_proto.text

    # 4. ACK with non-matching seq changes nothing
    pkt_wrong_ack = pack_gateway_packet(
        gateway_mac=gw_a.mac,
        node_id=node_p.node_no,
        counter=11,
        tilt_x=0.0,
        tilt_y=0.0,
        vib_rms=0.0,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
        last_cmd_seq=999,  # Non-matching seq
    )
    client.post(
        "/api/ingest",
        content=pkt_wrong_ack,
        headers={
            "Content-Type": "application/octet-stream",
            "X-Gateway-Key": env["raw_key_a"],
            "X-Forwarded-Proto": "https",
        },
    )
    db.refresh(cmd_a)
    assert cmd_a.acked_at is None  # Unchanged


def test_command_status_superseded_and_post_pending(client, db):
    """A7: POST command returns status 'pending'; superseded command reflects in GET."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    node = env["node_placed"]

    _login(client, mgr_a.id)

    # POST command response contains status "pending"
    r_post = client.post(f"/api/nodes/{node.id}/commands", json={"cmd": "buzzer_on"})
    assert r_post.status_code == 201
    data = r_post.json()
    assert data["status"] == "pending"
    cmd_id = data["id"]

    # Mark command superseded (expires_at shortened before created_at + COMMAND_TTL_S)
    cmd = db.query(NodeCommand).filter(NodeCommand.id == cmd_id).first()
    cmd.expires_at = cmd.created_at + datetime.timedelta(seconds=10)
    db.commit()

    # GET commands reflects status "superseded"
    r_get = client.get(f"/api/nodes/{node.id}/commands")
    assert r_get.status_code == 200
    cmds_data = r_get.json()["commands"]
    assert len(cmds_data) == 1
    assert cmds_data[0]["status"] == "superseded"


def test_incidents_pagination_and_roster_late_arrival(client, db, freeze_clock):
    """A6: GET /api/incidents pagination limit/offset and IncidentRosterItem late_arrival."""
    env = _setup_hardening_env(db)
    mgr_a = env["mgr_a"]
    miner_a = env["miner_a"]
    badge_token = env["badge_token"]
    tun = env["tun_a1"]

    t0 = datetime.datetime(2030, 9, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Create 3 incidents
    inc1 = open_incident(db, mgr_a, env["mine_a"].id, tun.id, "Inc 1", level=1, siren=False, now=t0)
    inc2 = open_incident(db, mgr_a, env["mine_a"].id, tun.id, "Inc 2", level=2, siren=False, now=t0 + datetime.timedelta(minutes=1))
    inc3 = open_incident(db, mgr_a, env["mine_a"].id, tun.id, "Inc 3", level=3, siren=False, now=t0 + datetime.timedelta(minutes=2))
    db.commit()

    _login(client, mgr_a.id)

    # Test limit=2, offset=0
    r_page1 = client.get("/api/incidents?limit=2&offset=0")
    assert r_page1.status_code == 200
    assert len(r_page1.json()["incidents"]) == 2

    # Test limit=2, offset=2
    r_page2 = client.get("/api/incidents?limit=2&offset=2")
    assert r_page2.status_code == 200
    assert len(r_page2.json()["incidents"]) == 1

    # Late arrival test: worker scans IN after incident opened
    t_scan_in = t0 + datetime.timedelta(minutes=10)
    freeze_clock(t_scan_in)
    record_scan(db, scanner=mgr_a, token=badge_token, direction="in", tunnel_id=tun.id, now=t_scan_in)
    db.commit()

    r_roster = client.get(f"/api/incidents/{inc1.id}/roster")
    assert r_roster.status_code == 200
    roster_items = r_roster.json()
    assert len(roster_items) == 1
    assert roster_items[0]["late_arrival"] is True
