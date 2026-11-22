"""Tests for Step 7: Incidents, Roll Call Snapshot, and Downlink Node Commands.

Verifies manual broadcast, roster snapshot, gate scan accounting, critical auto-incidents,
buzzer downlink commands, ingest delivery, seq wrapping, HTTPS enforcement, siren keepalive,
concurrency barrier, and complete Done-When user journey.
"""
import datetime
import hashlib
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import (
    COMMAND_TTL_S,
    DEMO_GATEWAY_KEY,
    SIREN_REFRESH_S,
)
from app.database import SessionLocal
from app.models.assignment import Assignment
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
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_MINER,
    ROLE_SAFETY_OFFICER,
)
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.badge_service import issue_badge
from app.services.checkin_service import record_scan
from app.services.hazard_classifier import classify_hazard
from app.services.incident_service import (
    auto_open_for_critical,
    close_incident,
    get_affected_nodes,
    mark_accounted,
    open_incident,
    set_siren,
)
from app.services.node_command_service import (
    create_command,
    mark_acked,
    pending_lines_for_gateway,
)
from app.services.packet_codec import pack_gateway_packet
from app.services.siren_service import run_siren_cycle


def _login(client, user_id: int):
    token = create_session_token(user_id)
    client.cookies.set(COOKIE_NAME, token)


def _setup_step7_env(db: Session) -> Dict[str, Any]:
    comp = Company(name="Step7 Coal Ltd", name_normalized="step7 coal ltd")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Shaft 7 Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel1 = Tunnel(mine_id=mine.id, name="East Incline", code="T-E1")
    tunnel2 = Tunnel(mine_id=mine.id, name="West Incline", code="T-W1")
    db.add_all([tunnel1, tunnel2])
    db.flush()

    # Hardware gateway with SHA-256 API key
    gw_key = DEMO_GATEWAY_KEY or "demo_gateway_key_32_chars_step7_test"
    gw_hash = hashlib.sha256(gw_key.encode("utf-8")).hexdigest()
    gateway = Gateway(
        mine_id=mine.id,
        name="Gateway E1",
        mac="AA:BB:CC:11:22:33",
        api_key_hash=gw_hash,
        active=True,
    )
    db.add(gateway)
    db.flush()

    # Placed sensor nodes
    node1 = Node(
        gateway_id=gateway.id,
        node_no=1,
        tunnel_id=tunnel1.id,
        lat=23.750,
        lon=86.420,
        status="normal",
        battery_pct=95,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    node2 = Node(
        gateway_id=gateway.id,
        node_no=2,
        tunnel_id=tunnel1.id,
        lat=23.751,
        lon=86.421,
        status="normal",
        battery_pct=90,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    db.add_all([node1, node2])
    db.flush()

    # Users
    mgr = User(company_id=comp.id, username="s7_mgr", full_name="Manager Seven", role=ROLE_MANAGER, password_hash=hash_password("Pass@12345"), active=True)
    so = User(company_id=comp.id, username="s7_so", full_name="Safety Officer Seven", role=ROLE_SAFETY_OFFICER, password_hash=hash_password("Pass@12345"), active=True)
    foreman = User(company_id=comp.id, username="s7_foreman", full_name="Foreman Seven", role=ROLE_FOREMAN, password_hash=hash_password("Pass@12345"), active=True)
    miner1 = User(company_id=comp.id, username="s7_miner1", full_name="Miner One", role=ROLE_MINER, password_hash=hash_password("Pass@12345"), active=True)
    miner2 = User(company_id=comp.id, username="s7_miner2", full_name="Miner Two", role=ROLE_MINER, password_hash=hash_password("Pass@12345"), active=True)
    miner3 = User(company_id=comp.id, username="s7_miner3", full_name="Miner Three", role=ROLE_MINER, password_hash=hash_password("Pass@12345"), active=True)
    db.add_all([mgr, so, foreman, miner1, miner2, miner3])
    db.flush()

    for u in (mgr, so, foreman, miner1, miner2, miner3):
        db.add(UserMine(user_id=u.id, mine_id=mine.id))

    db.add(WorkerProfile(company_id=comp.id, user_id=miner1.id, employee_no="EMP-701", phone="+919876543201"))
    db.add(WorkerProfile(company_id=comp.id, user_id=miner2.id, employee_no="EMP-702", phone="+919876543202"))
    db.add(WorkerProfile(company_id=comp.id, user_id=miner3.id, employee_no="EMP-703", phone="+919876543203"))
    db.add(WorkerProfile(company_id=comp.id, user_id=foreman.id, employee_no="EMP-704", phone="+919876543204"))
    db.commit()

    return {
        "company": comp,
        "mine": mine,
        "tunnel1": tunnel1,
        "tunnel2": tunnel2,
        "gateway": gateway,
        "gateway_key": gw_key,
        "node1": node1,
        "node2": node2,
        "manager": mgr,
        "safety_officer": so,
        "foreman": foreman,
        "miner1": miner1,
        "miner2": miner2,
        "miner3": miner3,
    }


def test_manual_incident_open_and_roster_snapshot(client, db, freeze_clock):
    """B7: Manual incident open snapshots exact headcount roster, unaccounted, and activates sirens."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    miner1 = env["miner1"]
    miner2 = env["miner2"]
    t1 = env["tunnel1"]

    t0 = datetime.datetime(2030, 8, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Miners check in to tunnel 1
    db.add(CheckEvent(user_id=miner1.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=t0))
    db.add(CheckEvent(user_id=miner2.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=t0))
    db.commit()

    # 2. Manager opens incident with siren
    t1_time = t0 + datetime.timedelta(minutes=15)
    freeze_clock(t1_time)
    _login(client, mgr.id)

    res = client.post(
        "/api/incidents",
        json={
            "mine_id": env["mine"].id,
            "tunnel_id": t1.id,
            "title": "Roof fracture detected on East Incline",
            "level": 3,
            "siren": True,
        },
    )
    assert res.status_code == 201
    inc_data = res.json()
    assert inc_data["status"] == "open"
    assert inc_data["siren_active"] is True
    assert inc_data["unaccounted_count"] == 2
    assert inc_data["total_count"] == 2

    # 3. Roster snapshot
    roster = client.get(f"/api/incidents/{inc_data['id']}/roster").json()
    assert len(roster) == 2
    user_ids = {r["user_id"] for r in roster}
    assert user_ids == {miner1.id, miner2.id}
    for r in roster:
        assert r["accounted_at"] is None

    # 4. Buzzer commands created for affected nodes
    cmds = db.query(NodeCommand).filter(NodeCommand.node_id.in_([env["node1"].id, env["node2"].id])).all()
    assert len(cmds) == 2
    for c in cmds:
        assert c.cmd == "buzzer_on"
        assert c.acked_at is None

    # 5. Audit log contains no incident title or worker names
    audit_row = db.execute(text("SELECT detail::text FROM audit_log WHERE entity = 'incident' AND entity_id = :id"), {"id": inc_data["id"]}).mappings().first()
    assert audit_row is not None
    assert "Roof fracture" not in audit_row["detail"]
    assert "Miner" not in audit_row["detail"]


def test_gate_scan_accounting_and_late_arrival(client, db, freeze_clock):
    """B7: Out scan at gate marks worker accounted; In scan adds worker to roster as unaccounted."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    miner1 = env["miner1"]
    miner3 = env["miner3"]
    t1 = env["tunnel1"]

    t0 = datetime.datetime(2030, 8, 2, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Miner 1 is inside
    db.add(CheckEvent(user_id=miner1.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=t0))
    db.commit()

    # Open incident
    inc = open_incident(db, mgr, env["mine"].id, t1.id, "Emergency Test", level=2, siren=False, now=t0)
    db.commit()

    # Issue badge for miner 1 and miner 3
    _, _, q1 = issue_badge(db, mgr, miner1.id)
    _, _, q3 = issue_badge(db, mgr, miner3.id)
    tok1 = f"cmb_tok_{miner1.id}"
    tok3 = f"cmb_tok_{miner3.id}"
    q1.token_hash = hashlib.sha256(tok1.encode("utf-8")).hexdigest()
    q3.token_hash = hashlib.sha256(tok3.encode("utf-8")).hexdigest()
    db.commit()

    # 1. Miner 1 scans OUT at gate -> automatically accounted
    t_out = t0 + datetime.timedelta(minutes=5)
    record_scan(db, mgr, tok1, "out", tunnel_id=t1.id, now=t_out)

    roster_entry1 = db.query(IncidentRoster).filter(IncidentRoster.incident_id == inc.id, IncidentRoster.user_id == miner1.id).first()
    assert roster_entry1.accounted_at is not None
    assert roster_entry1.accounted_by == mgr.id

    # 2. Miner 3 (not previously inside) scans IN during incident -> added as unaccounted
    t_in3 = t0 + datetime.timedelta(minutes=10)
    record_scan(db, mgr, tok3, "in", tunnel_id=t1.id, now=t_in3)

    roster_entry3 = db.query(IncidentRoster).filter(IncidentRoster.incident_id == inc.id, IncidentRoster.user_id == miner3.id).first()
    assert roster_entry3 is not None
    assert roster_entry3.accounted_at is None


def test_manual_roll_call_accounting_and_foreman_scoping(client, db, freeze_clock):
    """B7: Manual account button marks accounted; foreman sees only own-tunnel incidents; miners 403."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner1 = env["miner1"]
    t1 = env["tunnel1"]
    t2 = env["tunnel2"]

    now = datetime.datetime(2030, 8, 3, 9, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    # Foreman assigned to tunnel 1
    db.add(Assignment(user_id=foreman.id, tunnel_id=t1.id, starts_at=now - datetime.timedelta(hours=1), ends_at=now + datetime.timedelta(hours=7)))
    # Miner 1 checked into t1
    db.add(CheckEvent(user_id=miner1.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=now - datetime.timedelta(minutes=30)))
    db.commit()

    inc1 = open_incident(db, mgr, env["mine"].id, t1.id, "T1 Incident", level=2, siren=False, now=now)
    inc2 = open_incident(db, mgr, env["mine"].id, t2.id, "T2 Incident", level=2, siren=False, now=now)
    db.commit()

    # 1. Miner gets 403 on incidents
    _login(client, miner1.id)
    res_m = client.get("/api/incidents")
    assert res_m.status_code == 403

    # 2. Foreman sees only T1 incident
    _login(client, foreman.id)
    res_f = client.get("/api/incidents").json()
    assert len(res_f["incidents"]) == 1
    assert res_f["incidents"][0]["id"] == inc1.id

    # Foreman accessing T2 incident -> 404
    res_f_t2 = client.get(f"/api/incidents/{inc2.id}")
    assert res_f_t2.status_code == 404

    # 3. Foreman accounts worker in T1 incident
    res_acc = client.post(f"/api/incidents/{inc1.id}/roster/{miner1.id}/account")
    assert res_acc.status_code == 200

    r_row = db.query(IncidentRoster).filter(IncidentRoster.incident_id == inc1.id, IncidentRoster.user_id == miner1.id).first()
    assert r_row.accounted_at is not None
    assert r_row.accounted_by == foreman.id

    # Foreman cannot close incident (superiors only)
    res_f_close = client.patch(f"/api/incidents/{inc1.id}", json={"close": True})
    assert res_f_close.status_code == 403


def test_close_incident_unaccounted_conflict_and_confirm(client, db, freeze_clock):
    """B7: Close incident blocked with 409 when unaccounted remain without confirm; succeeds with confirm."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    miner1 = env["miner1"]
    t1 = env["tunnel1"]

    now = datetime.datetime(2030, 8, 4, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    db.add(CheckEvent(user_id=miner1.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=now))
    db.commit()

    inc = open_incident(db, mgr, env["mine"].id, t1.id, "Evac Event", level=3, siren=True, now=now)
    db.commit()

    _login(client, mgr.id)

    # 1. Close without confirm -> 409
    res_close_err = client.patch(f"/api/incidents/{inc.id}", json={"close": True, "confirm_unaccounted": False})
    assert res_close_err.status_code == 409
    assert res_close_err.json()["detail"]["code"] == "unaccounted_workers"
    assert res_close_err.json()["detail"]["count"] == 1

    # 2. Close with confirm -> 200
    res_close_ok = client.patch(
        f"/api/incidents/{inc.id}",
        json={
            "close": True,
            "confirm_unaccounted": True,
            "close_note": "Confirmed left safely through Portal B",
        },
    )
    assert res_close_ok.status_code == 200
    assert res_close_ok.json()["status"] == "closed"
    assert res_close_ok.json()["siren_active"] is False

    # Check buzzer_off command dispatched
    off_cmds = db.query(NodeCommand).filter(NodeCommand.node_id == env["node1"].id, NodeCommand.cmd == "buzzer_off").all()
    assert len(off_cmds) == 1

    # Check audit log does not leak close_note
    audit_row = db.execute(text("SELECT detail::text FROM audit_log WHERE action = 'incident.close' AND entity_id = :id"), {"id": inc.id}).mappings().first()
    assert audit_row is not None
    assert "Portal B" not in audit_row["detail"]


def test_auto_open_incident_for_critical_reading(client, db, freeze_clock):
    """B7: Placed node level transitioning to 3 auto-opens incident with siren; second node does not duplicate."""
    env = _setup_step7_env(db)
    node1 = env["node1"]
    node2 = env["node2"]
    gw = env["gateway"]

    now = datetime.datetime(2030, 8, 5, 11, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    # 1. Node 1 level transition 1 -> 3
    r1 = Reading(
        node_id=node1.id,
        ts=now,
        boot_id=0,
        counter=1,
        tilt_x=0.5,
        tilt_y=0.0,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        node_level=1,
        level=1,
    )
    db.add(r1)
    db.commit()

    r2 = Reading(
        node_id=node1.id,
        ts=now + datetime.timedelta(seconds=5),
        boot_id=0,
        counter=2,
        tilt_x=2.5,
        tilt_y=0.0,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        node_level=3,
        level=3,
    )
    db.add(r2)
    db.flush()

    inc = auto_open_for_critical(db, node1, r2, prev_level=1, now=now + datetime.timedelta(seconds=5))
    db.commit()

    assert inc is not None
    assert inc.source == "auto"
    assert inc.level == 3
    assert inc.siren_active is True
    assert inc.tunnel_id == node1.tunnel_id

    # 2. Second node reaching level 3 in same tunnel -> no second incident (idempotent)
    r_node2 = Reading(
        node_id=node2.id,
        ts=now + datetime.timedelta(seconds=10),
        boot_id=0,
        counter=1,
        tilt_x=2.5,
        tilt_y=0.0,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        node_level=3,
        level=3,
    )
    db.add(r_node2)
    db.flush()

    inc2 = auto_open_for_critical(db, node2, r_node2, prev_level=0, now=now + datetime.timedelta(seconds=10))
    assert inc2.id == inc.id  # Same incident returned, no duplicate created

    # Total open auto incidents for this tunnel is exactly 1
    total_auto = db.query(Incident).filter(Incident.tunnel_id == node1.tunnel_id, Incident.status == "open", Incident.source == "auto").count()
    assert total_auto == 1


def test_node_commands_delivery_in_ingest_and_echo_ack(client, db, freeze_clock):
    """B7: Pending command delivered in next ingest response; gateway echo marks command acked."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    node1 = env["node1"]
    gw = env["gateway"]
    gw_key = env["gateway_key"]

    now = datetime.datetime(2030, 8, 6, 12, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    # 1. Superior creates buzzer_on command
    _login(client, mgr.id)
    res_cmd = client.post(f"/api/nodes/{node1.id}/commands", json={"cmd": "buzzer_on"})
    assert res_cmd.status_code == 201
    cmd_data = res_cmd.json()
    seq = cmd_data["seq"]

    # 2. Ingest over HTTPS delivers plain-text command lines
    packet_bytes = pack_gateway_packet(
        gateway_mac="AA:BB:CC:11:22:33",
        node_id=1,
        counter=10,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=0,
        batt_v=4.0,
        batt_pct=90,
        danger_level=0,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )

    res_ingest = client.post(
        "https://testserver/api/ingest",
        content=packet_bytes,
        headers={"X-Gateway-Key": gw_key},
    )
    assert res_ingest.status_code == 200
    assert res_ingest.headers["content-type"].startswith("text/plain")
    expected_line = f"CMD,1,BUZZER_ON,{seq}\n"
    assert res_ingest.text == expected_line

    # 3. Gateway transmits packet echoing last_cmd_seq = seq -> marked acked
    freeze_clock(now + datetime.timedelta(seconds=5))
    packet_echo = pack_gateway_packet(
        gateway_mac="AA:BB:CC:11:22:33",
        node_id=1,
        counter=11,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=0,
        batt_v=4.0,
        batt_pct=90,
        danger_level=0,
        fast_mode=0,
        buzzer_on=1,
        last_cmd_seq=seq,
    )
    res_echo = client.post(
        "https://testserver/api/ingest",
        content=packet_echo,
        headers={"X-Gateway-Key": gw_key},
    )
    assert res_echo.status_code == 200
    assert res_echo.text == ""  # Command was acked, no pending lines remain

    # Verify command in DB is acked
    cmd_db = db.query(NodeCommand).filter(NodeCommand.id == cmd_data["id"]).first()
    assert cmd_db.acked_at is not None


def test_command_expiry_and_pending_replacement(client, db, freeze_clock):
    """B7: Unacked command past COMMAND_TTL_S is expired; new command expires older pending."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    node1 = env["node1"]
    gw_key = env["gateway_key"]

    t0 = datetime.datetime(2030, 8, 7, 14, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Issue command 1
    c1 = create_command(db, mgr, node1, "buzzer_on", now=t0)
    db.commit()

    # 2. Advance time past TTL (TTL=120s) -> unacked command is expired and not delivered
    t_expired = t0 + datetime.timedelta(seconds=COMMAND_TTL_S + 10)
    freeze_clock(t_expired)

    lines = pending_lines_for_gateway(db, env["gateway"].id, now=t_expired)
    assert lines == ""

    # 3. New command expires older pending
    c2 = create_command(db, mgr, node1, "buzzer_off", now=t_expired)
    db.commit()

    db.refresh(c1)
    assert c1.expires_at <= t_expired  # Expired
    assert c2.seq == c1.seq + 1


def test_command_seq_wrapping(db, freeze_clock):
    """B7: Command seq wraps 65535 -> 1 and never uses 0."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    node1 = env["node1"]

    now = datetime.datetime(2030, 8, 8, 15, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    # Manually create command with seq 65535
    c_max = NodeCommand(
        node_id=node1.id,
        cmd="buzzer_on",
        seq=65535,
        created_by=mgr.id,
        created_at=now,
        expires_at=now + datetime.timedelta(seconds=120),
    )
    db.add(c_max)
    db.commit()

    # Create next command
    c_next = create_command(db, mgr, node1, "buzzer_off", now=now)
    db.commit()

    assert c_next.seq == 1


def test_https_commands_withholding(client, db, freeze_clock, caplog):
    """B7: Plaintext ingest with COMMANDS_REQUIRE_HTTPS=True withholds commands; HTTPS delivers them."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    node1 = env["node1"]
    gw_key = env["gateway_key"]

    now = datetime.datetime(2030, 8, 9, 16, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    create_command(db, mgr, node1, "buzzer_on", now=now)
    db.commit()

    packet = pack_gateway_packet(
        gateway_mac="AA:BB:CC:11:22:33",
        node_id=1,
        counter=1,
        tilt_x=0.0,
        tilt_y=0.0,
        vib_rms=0.0,
        shock=0,
        batt_v=4.0,
        batt_pct=100,
        danger_level=0,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )

    # 1. Plaintext HTTP -> commands withheld, warning logged
    with caplog.at_level(logging.WARNING, logger="security"):
        res_http = client.post(
            "http://testserver/api/ingest",
            content=packet,
            headers={"X-Gateway-Key": gw_key},
        )
        assert res_http.status_code == 200
        assert res_http.text == ""  # Withheld
        assert any("Withholding node commands" in r.message for r in caplog.records)

    # 2. HTTPS (or trusted X-Forwarded-Proto) -> commands delivered
    res_https = client.post(
        "https://testserver/api/ingest",
        content=packet,
        headers={"X-Gateway-Key": gw_key},
    )
    assert res_https.status_code == 200
    assert "CMD,1,BUZZER_ON" in res_https.text


def test_siren_keepalive_refresh_cycle(db, freeze_clock):
    """B7: Creates new buzzer_on command after SIREN_REFRESH_S for open siren incidents."""
    env = _setup_step7_env(db)
    mgr = env["manager"]
    t1 = env["tunnel1"]
    node1 = env["node1"]

    t0 = datetime.datetime(2030, 8, 10, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    inc = open_incident(db, mgr, env["mine"].id, t1.id, "Siren Refresh Test", level=3, siren=True, now=t0)
    db.commit()

    # At T0 + 20s (< SIREN_REFRESH_S=45): 0 refreshed
    freeze_clock(t0 + datetime.timedelta(seconds=20))
    assert run_siren_cycle(db, now=t0 + datetime.timedelta(seconds=20)) == 0

    # At T0 + 50s (> SIREN_REFRESH_S): refreshed count = 2 (for 2 placed nodes in tunnel)
    t_refresh = t0 + datetime.timedelta(seconds=50)
    freeze_clock(t_refresh)
    assert run_siren_cycle(db, now=t_refresh) == 2


def test_role_matrix_and_scoping_isolation(client, db):
    """B7: Miners 403; foreman cannot open/close/issue commands; cross-company 404."""
    env = _setup_step7_env(db)
    foreman = env["foreman"]
    miner = env["miner1"]
    node1 = env["node1"]

    # 1. Miner gets 403 on commands and incidents
    _login(client, miner.id)
    assert client.post(f"/api/nodes/{node1.id}/commands", json={"cmd": "buzzer_on"}).status_code == 403
    assert client.get(f"/api/nodes/{node1.id}/commands").status_code == 403
    assert client.post("/api/incidents", json={"mine_id": env["mine"].id, "title": "Test", "level": 1}).status_code == 403

    # 2. Foreman gets 403 on commands
    _login(client, foreman.id)
    assert client.post(f"/api/nodes/{node1.id}/commands", json={"cmd": "buzzer_on"}).status_code == 403
    assert client.get(f"/api/nodes/{node1.id}/commands").status_code == 403


def test_dual_thread_concurrent_command_race(db):
    """B7: Concurrent command creation produces strictly increasing seq and one pending command."""
    env = _setup_step7_env(db)
    mgr_id = env["manager"].id
    node1_id = env["node1"].id

    barrier = threading.Barrier(2)
    results = []

    def dispatch():
        thread_db = SessionLocal()
        try:
            m = thread_db.query(User).filter(User.id == mgr_id).first()
            n = thread_db.query(Node).filter(Node.id == node1_id).first()
            barrier.wait()
            cmd = create_command(thread_db, m, n, "buzzer_on")
            thread_db.commit()
            results.append(("ok", cmd.seq))
        except Exception as e:
            thread_db.rollback()
            results.append(("err", str(e)))
        finally:
            thread_db.close()

    t1 = threading.Thread(target=dispatch)
    t2 = threading.Thread(target=dispatch)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert all(r[0] == "ok" for r in results)
    seqs = {r[1] for r in results}
    assert len(seqs) == 2  # Different, strictly increasing seqs

    # Exactly one unexpired pending command
    now = datetime.datetime.now(datetime.timezone.utc)
    pending = db.query(NodeCommand).filter(NodeCommand.node_id == node1_id, NodeCommand.acked_at.is_(None), NodeCommand.expires_at > now).all()
    assert len(pending) == 1


def test_alembic_downgrade_and_upgrade_step7():
    """B7: Tests clean Alembic downgrade -1 followed by upgrade head."""
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    cfg = Config(str(alembic_ini_path))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    try:
        command.downgrade(cfg, "-1")
    finally:
        command.upgrade(cfg, "head")
        command.check(cfg)


def test_step7_done_when_end_to_end(client, db, freeze_clock):
    """B7: Full Step 7 Done-When Journey:

    Superior opens incident; roll call snapshots checked-in workers; gate out-scan accounts worker;
    unaccounted stays highlighted; buzzer commands delivered in ingest; node echoes last_cmd_seq marking done;
    superior closes incident confirming roll call.
    """
    env = _setup_step7_env(db)
    mgr = env["manager"]
    miner1 = env["miner1"]
    miner2 = env["miner2"]
    t1 = env["tunnel1"]
    gw_key = env["gateway_key"]

    t0 = datetime.datetime(2030, 9, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Miners check in at tunnel 1
    db.add(CheckEvent(user_id=miner1.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=t0))
    db.add(CheckEvent(user_id=miner2.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=t0))
    db.commit()

    # Issue badge for miner 1
    _, _, q1 = issue_badge(db, mgr, miner1.id)
    tok1 = f"cmb_tok_{miner1.id}"
    q1.token_hash = hashlib.sha256(tok1.encode("utf-8")).hexdigest()
    db.commit()

    # 2. Superior opens incident for tunnel 1 with siren
    _login(client, mgr.id)
    t_open = t0 + datetime.timedelta(minutes=10)
    freeze_clock(t_open)

    res_open = client.post(
        "/api/incidents",
        json={
            "mine_id": env["mine"].id,
            "tunnel_id": t1.id,
            "title": "Subsidence fracture active at East Incline",
            "level": 3,
            "siren": True,
        },
    )
    assert res_open.status_code == 201
    inc_id = res_open.json()["id"]

    # 3. Roll call lists both workers as unaccounted
    roster_res = client.get(f"/api/incidents/{inc_id}/roster").json()
    assert len(roster_res) == 2
    assert all(r["accounted_at"] is None for r in roster_res)

    # 4. Miner 1 scans out at gate -> automatically marked accounted
    t_scan_out = t0 + datetime.timedelta(minutes=11)
    freeze_clock(t_scan_out)
    record_scan(db, mgr, tok1, "out", tunnel_id=t1.id, now=t_scan_out)

    roster_res2 = client.get(f"/api/incidents/{inc_id}/roster").json()
    acc_m1 = next(r for r in roster_res2 if r["user_id"] == miner1.id)
    unacc_m2 = next(r for r in roster_res2 if r["user_id"] == miner2.id)
    assert acc_m1["accounted_at"] is not None
    assert unacc_m2["accounted_at"] is None  # Still unaccounted

    # 5. Gateway posts ingest -> buzzer_on command delivered in HTTP response
    node1_cmd = db.query(NodeCommand).filter(NodeCommand.node_id == env["node1"].id, NodeCommand.acked_at.is_(None)).first()
    assert node1_cmd is not None
    seq = node1_cmd.seq

    packet_ingest = pack_gateway_packet(
        gateway_mac="AA:BB:CC:11:22:33",
        node_id=1,
        counter=1,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=0,
        batt_v=4.0,
        batt_pct=95,
        danger_level=0,
        fast_mode=0,
        buzzer_on=0,
        last_cmd_seq=0,
    )
    res_ingest = client.post(
        "https://testserver/api/ingest",
        content=packet_ingest,
        headers={"X-Gateway-Key": gw_key},
    )
    assert res_ingest.status_code == 200
    assert f"CMD,1,BUZZER_ON,{seq}\n" in res_ingest.text

    # 6. Gateway echoes last_cmd_seq = seq -> marks command done
    packet_echo = pack_gateway_packet(
        gateway_mac="AA:BB:CC:11:22:33",
        node_id=1,
        counter=2,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=0,
        batt_v=4.0,
        batt_pct=95,
        danger_level=0,
        fast_mode=0,
        buzzer_on=1,
        last_cmd_seq=seq,
    )
    res_echo = client.post(
        "https://testserver/api/ingest",
        content=packet_echo,
        headers={"X-Gateway-Key": gw_key},
    )
    assert res_echo.status_code == 200

    db.refresh(node1_cmd)
    assert node1_cmd.acked_at is not None

    # 7. Miner 2 is accounted manually
    res_acc2 = client.post(f"/api/incidents/{inc_id}/roster/{miner2.id}/account")
    assert res_acc2.status_code == 200

    # 8. Superior closes incident
    res_close = client.patch(
        f"/api/incidents/{inc_id}",
        json={"close": True, "confirm_unaccounted": True, "close_note": "Evacuation complete"},
    )
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "closed"
    assert res_close.json()["siren_active"] is False
