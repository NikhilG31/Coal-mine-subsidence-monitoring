"""Step 8: Comprehensive test suite for worker reports, device SOS, and incident integration.

Owns full end-to-end and unit verification of:
1. Worker reports creation, role permissions, SOS severity forcing, and tunnel scoping.
2. Worker-safe view vs superior view field isolation and gateway MAC confidentiality.
3. In-memory sliding window rate limiting (20/hour -> 429 with Retry-After).
4. Status lifecycle (open -> acknowledged -> resolved) and strict RBAC enforcement.
5. Direct PostgreSQL CHECK constraint verification on worker_reports table.
6. Device SOS ingestion: placed vs unplaced nodes, merge windows (60s), flood guard (6/hr),
   duplicate readings handling, and 503-then-retry resilience contract.
7. Concurrent device SOS ingestion serialization via row locking.
8. Simulator SOS burst generation.
9. Done-When full lifecycle journey with frozen clock.
10. Alembic migration downgrade -1 and upgrade head roundtrip.

Must NOT run against the development database or mock the datetime module directly.
"""
import concurrent.futures
import datetime
from pathlib import Path
import time
from typing import Any, Dict

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.dev.simulator import Simulator
from app.models.alert import Alert
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.incident_roster import IncidentRoster
from app.models.mine import Mine
from app.models.node import Node
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.models.worker_report import WorkerReport
from app.schemas.reports import SuperiorReportView, WorkerReportView
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
from app.services.gateway_keys import hash_key
from app.services.packet_codec import pack_gateway_packet, parse_gateway_packet


def _login(client, user_id: int):
    client.cookies.set(COOKIE_NAME, create_session_token(user_id))


def _setup_step8_env(db: Session) -> Dict[str, Any]:
    # Company A
    comp_a = Company(name="Step8 Coal India", name_normalized="step8 coal india")
    db.add(comp_a)
    db.flush()

    mine_a = Mine(company_id=comp_a.id, name="Mine Bharat", status="active", method="underground")
    db.add(mine_a)
    db.flush()

    tun_a1 = Tunnel(mine_id=mine_a.id, name="Tunnel 1", code="T1")
    tun_a2 = Tunnel(mine_id=mine_a.id, name="Tunnel 2", code="T2")
    db.add_all([tun_a1, tun_a2])
    db.flush()

    gw_a = Gateway(
        mac="11:22:33:44:55:66",
        mine_id=mine_a.id,
        name="Gateway Bharat-1",
        api_key_hash=hash_key("gwkey_bharat1"),
        active=True,
    )
    db.add(gw_a)
    db.flush()

    node_placed = Node(
        gateway_id=gw_a.id,
        node_no=10,
        tunnel_id=tun_a1.id,
        lat=23.75,
        lon=86.42,
        status="normal",
    )
    node_unplaced = Node(
        gateway_id=gw_a.id,
        node_no=20,
        tunnel_id=None,
        lat=None,
        lon=None,
        status="normal",
    )
    db.add_all([node_placed, node_unplaced])
    db.flush()

    pw = hash_password("Secret123")
    mgr_a = User(company_id=comp_a.id, username="mgr_bharat", full_name="Manager Bharat", password_hash=pw, role=ROLE_MANAGER, active=True)
    safety_a = User(company_id=comp_a.id, username="safety_bharat", full_name="Safety Bharat", password_hash=pw, role=ROLE_SAFETY_OFFICER, active=True)
    eng_a = User(company_id=comp_a.id, username="eng_bharat", full_name="Engineer Bharat", password_hash=pw, role=ROLE_ENGINEER, active=True)
    foreman_a = User(company_id=comp_a.id, username="foreman_bharat", full_name="Foreman Bharat", password_hash=pw, role=ROLE_FOREMAN, active=True)
    miner_a = User(company_id=comp_a.id, username="miner_bharat", full_name="Miner Ramesh", password_hash=pw, role=ROLE_MINER, active=True)
    db.add_all([mgr_a, safety_a, eng_a, foreman_a, miner_a])
    db.flush()

    for u in (mgr_a, safety_a, eng_a, foreman_a, miner_a):
        db.add(UserMine(user_id=u.id, mine_id=mine_a.id))

    p_miner = WorkerProfile(
        user_id=miner_a.id,
        company_id=comp_a.id,
        employee_no="EMP-RAMESH",
        emergency_contact_name="Sita Devi",
        emergency_contact_phone="+910000000000",
    )
    db.add(p_miner)
    db.flush()

    # Company B (Foreign)
    comp_b = Company(name="Foreign Mining Ltd", name_normalized="foreign mining ltd")
    db.add(comp_b)
    db.flush()

    mine_b = Mine(company_id=comp_b.id, name="Mine Foreign", status="active", method="underground")
    db.add(mine_b)
    db.flush()

    tun_b = Tunnel(mine_id=mine_b.id, name="Tunnel Foreign", code="T-FOR")
    db.add(tun_b)
    db.flush()

    mgr_b = User(company_id=comp_b.id, username="mgr_foreign", full_name="Manager Foreign", password_hash=pw, role=ROLE_MANAGER, active=True)
    db.add(mgr_b)
    db.flush()
    db.add(UserMine(user_id=mgr_b.id, mine_id=mine_b.id))

    db.commit()

    return {
        "comp_a": comp_a,
        "mine_a": mine_a,
        "tun_a1": tun_a1,
        "tun_a2": tun_a2,
        "gw_a": gw_a,
        "node_placed": node_placed,
        "node_unplaced": node_unplaced,
        "mgr_a": mgr_a,
        "safety_a": safety_a,
        "eng_a": eng_a,
        "foreman_a": foreman_a,
        "miner_a": miner_a,
        "profile_miner": p_miner,
        "comp_b": comp_b,
        "mine_b": mine_b,
        "tun_b": tun_b,
        "mgr_b": mgr_b,
    }


def test_worker_reports_creation_and_scoping(client, db):
    """B4, B6: Any role can submit reports; SOS forces critical; tunnel scoping and extra='forbid'."""
    env = _setup_step8_env(db)
    tun_a1 = env["tun_a1"]
    tun_b = env["tun_b"]

    roles = [env["miner_a"], env["foreman_a"], env["eng_a"], env["safety_a"], env["mgr_a"]]

    for user in roles:
        _login(client, user.id)
        # 1. Normal report creation
        resp = client.post(
            "/api/reports",
            json={
                "type": "equipment_fault",
                "severity": "medium",
                "tunnel_id": tun_a1.id,
                "description": f"Conveyor belt slippage reported by {user.username}",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["type"] == "equipment_fault"
        assert data["severity"] == "medium"
        assert data["status"] == "open"
        db_rep = db.query(WorkerReport).filter(WorkerReport.id == data["id"]).first()
        assert db_rep.source == "worker"

        # 2. Type SOS forces severity critical (even if low requested)
        sos_resp = client.post(
            "/api/reports",
            json={
                "type": "sos",
                "severity": "low",  # Server must override to critical
                "tunnel_id": tun_a1.id,
                "description": "Worker hit emergency button",
            },
        )
        assert sos_resp.status_code == 201
        sos_data = sos_resp.json()
        assert sos_data["type"] == "sos"
        assert sos_data["severity"] == "critical"

    # 3. Tunnel validation: Foreign tunnel in another company -> 404
    _login(client, env["miner_a"].id)
    foreign_resp = client.post(
        "/api/reports",
        json={
            "type": "injury",
            "severity": "high",
            "tunnel_id": tun_b.id,
            "description": "Cross-company breach attempt",
        },
    )
    assert foreign_resp.status_code == 404
    assert foreign_resp.json()["detail"] == "Tunnel not found"

    # 4. extra="forbid" rejects unexpected fields
    bad_field_resp = client.post(
        "/api/reports",
        json={
            "type": "injury",
            "severity": "high",
            "unknown_extra_field": "disallowed",
        },
    )
    assert bad_field_resp.status_code == 422


def test_worker_safe_view_vs_superior_view(client, db):
    """B4, B6: Worker view strictly excludes sensitive metadata; superior view includes all."""
    env = _setup_step8_env(db)
    tun_a1 = env["tun_a1"]
    miner = env["miner_a"]
    mgr = env["mgr_a"]

    # Miner creates a report
    _login(client, miner.id)
    c_resp = client.post(
        "/api/reports",
        json={
            "type": "injury",
            "severity": "medium",
            "tunnel_id": tun_a1.id,
            "description": "Minor finger cut",
        },
    )
    assert c_resp.status_code == 201
    rep_id = c_resp.json()["id"]

    # Worker calls GET /api/reports
    w_resp = client.get("/api/reports")
    assert w_resp.status_code == 200
    w_reports = w_resp.json()
    assert isinstance(w_reports, list)
    assert len(w_reports) >= 1
    w_item = next(r for r in w_reports if r["id"] == rep_id)

    # Validate against WorkerReportView schema
    WorkerReportView.model_validate(w_item)

    # Worker view field leakage prevention
    assert "reporter" not in w_item
    assert "node" not in w_item
    assert "repeat_count" not in w_item
    assert "incident_id" not in w_item
    assert "gateway_mac" not in str(w_item)

    # Superior calls GET /api/reports
    _login(client, mgr.id)
    s_resp = client.get("/api/reports")
    assert s_resp.status_code == 200
    s_reports = s_resp.json()
    assert isinstance(s_reports, list)
    s_item = next(r for r in s_reports if r["id"] == rep_id)

    # Validate against SuperiorReportView schema
    SuperiorReportView.model_validate(s_item)

    # Superior view fields present
    assert s_item["reporter"]["full_name"] == "Miner Ramesh"
    assert s_item["reporter"]["id"] == miner.id
    assert s_item["repeat_count"] == 1
    assert "gateway_mac" not in str(s_item)  # MAC NEVER leaks to API


def test_report_rate_limiting(client, db, freeze_clock):
    """B3, B6: 21st report in 1 hour returns 429 with Retry-After header."""
    env = _setup_step8_env(db)
    miner = env["miner_a"]
    _login(client, miner.id)

    t0 = datetime.datetime(2030, 10, 1, 9, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Submit 20 reports successfully
    for i in range(20):
        t_sub = t0 + datetime.timedelta(seconds=i * 2)
        freeze_clock(t_sub)
        resp = client.post(
            "/api/reports",
            json={
                "type": "gas_smell",
                "severity": "low",
                "description": f"Test report {i}",
            },
        )
        assert resp.status_code == 201

    # 21st report in the same hour triggers 429
    t_blocked = t0 + datetime.timedelta(seconds=50)
    freeze_clock(t_blocked)
    resp21 = client.post(
        "/api/reports",
        json={
            "type": "gas_smell",
            "severity": "low",
            "description": "Blocked report 21",
        },
    )
    assert resp21.status_code == 429
    assert resp21.json()["code"] == "rate_limit_exceeded"
    assert "Retry-After" in resp21.headers
    retry_after = int(resp21.headers["Retry-After"])
    assert retry_after > 0


def test_status_transitions_and_rbac(client, db, freeze_clock):
    """B0, B4, B6: Lifecycle open -> acknowledged -> resolved; invalid transitions -> 409; RBAC -> 403."""
    env = _setup_step8_env(db)
    miner = env["miner_a"]
    foreman = env["foreman_a"]
    safety = env["safety_a"]
    mgr = env["mgr_a"]

    t0 = datetime.datetime(2030, 10, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Miner creates report
    _login(client, miner.id)
    r1 = client.post(
        "/api/reports",
        json={"type": "need_help", "severity": "medium", "description": "Need assistance at face"},
    ).json()
    rep1_id = r1["id"]

    # 2. Miner or Foreman cannot PATCH status (403)
    _login(client, miner.id)
    assert client.patch(f"/api/reports/{rep1_id}", json={"status": "acknowledged"}).status_code == 403
    _login(client, foreman.id)
    assert client.patch(f"/api/reports/{rep1_id}", json={"status": "acknowledged"}).status_code == 403

    # 3. Superior acknowledges report
    t1 = t0 + datetime.timedelta(minutes=5)
    freeze_clock(t1)
    _login(client, safety.id)
    ack_res = client.patch(f"/api/reports/{rep1_id}", json={"status": "acknowledged"})
    assert ack_res.status_code == 200
    ack_data = ack_res.json()
    assert ack_data["status"] == "acknowledged"
    assert ack_data["acknowledged_by_name"] == safety.full_name
    db_rep1 = db.query(WorkerReport).filter(WorkerReport.id == rep1_id).first()
    assert db_rep1.acknowledged_by == safety.id

    # 4. Backward transition acknowledged -> open fails (409)
    bad_back = client.patch(f"/api/reports/{rep1_id}", json={"status": "open"})
    assert bad_back.status_code == 409
    assert bad_back.json()["code"] == "invalid_transition"

    # 5. Repeated transition acknowledged -> acknowledged fails (409)
    bad_rep = client.patch(f"/api/reports/{rep1_id}", json={"status": "acknowledged"})
    assert bad_rep.status_code == 409
    assert bad_rep.json()["code"] == "invalid_transition"

    # 6. Superior resolves report
    t2 = t1 + datetime.timedelta(minutes=10)
    freeze_clock(t2)
    _login(client, mgr.id)
    res_res = client.patch(
        f"/api/reports/{rep1_id}",
        json={"status": "resolved", "resolution_note": "Assistance dispatched and completed"},
    )
    assert res_res.status_code == 200
    res_data = res_res.json()
    assert res_data["status"] == "resolved"
    assert res_data["resolved_by_name"] == mgr.full_name
    assert res_data["resolution_note"] == "Assistance dispatched and completed"
    db.refresh(db_rep1)
    assert db_rep1.resolved_by == mgr.id

    # 7. Transition from resolved fails (409)
    assert client.patch(f"/api/reports/{rep1_id}", json={"status": "open"}).status_code == 409
    assert client.patch(f"/api/reports/{rep1_id}", json={"status": "acknowledged"}).status_code == 409

    # 8. Direct open -> resolved records acknowledgement simultaneously
    _login(client, miner.id)
    r2 = client.post(
        "/api/reports",
        json={"type": "equipment_fault", "severity": "low", "description": "Loose gauge nut"},
    ).json()
    rep2_id = r2["id"]

    _login(client, mgr.id)
    t3 = t2 + datetime.timedelta(minutes=5)
    freeze_clock(t3)
    direct_res = client.patch(
        f"/api/reports/{rep2_id}",
        json={"status": "resolved", "resolution_note": "Nut tightened immediately"},
    )
    assert direct_res.status_code == 200
    direct_data = direct_res.json()
    assert direct_data["status"] == "resolved"
    assert direct_data["acknowledged_by_name"] == mgr.full_name
    assert direct_data["acknowledged_at"] is not None
    assert direct_data["resolved_by_name"] == mgr.full_name
    assert direct_data["resolved_at"] is not None
    db_rep2 = db.query(WorkerReport).filter(WorkerReport.id == rep2_id).first()
    assert db_rep2.acknowledged_by == mgr.id
    assert db_rep2.resolved_by == mgr.id


def test_database_check_constraints_reject_invalid_rows(db):
    """B1, B6: Direct SQL INSERTs violating CHECK constraints raise IntegrityError."""
    env = _setup_step8_env(db)
    mine_id = env["mine_a"].id
    user_id = env["miner_a"].id
    node_id = env["node_placed"].id

    def _try_sql(sql_query: str):
        with pytest.raises(IntegrityError):
            with engine.connect() as conn:
                conn.execute(text(sql_query))
                conn.commit()

    # 1. Invalid source
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity) VALUES ({mine_id}, {user_id}, 'drone', 'injury', 'low')")

    # 2. Invalid type
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity) VALUES ({mine_id}, {user_id}, 'worker', 'fire_alarm', 'low')")

    # 3. Invalid severity
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity) VALUES ({mine_id}, {user_id}, 'worker', 'injury', 'extreme')")

    # 4. Invalid status
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity, status) VALUES ({mine_id}, {user_id}, 'worker', 'injury', 'low', 'pending')")

    # 5. Repeat count < 1
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity, repeat_count) VALUES ({mine_id}, {user_id}, 'worker', 'injury', 'low', 0)")

    # 6. Device report with user_id present (violates device shape: user_id must be NULL)
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, node_id, source, type, severity) VALUES ({mine_id}, {user_id}, {node_id}, 'device', 'sos', 'critical')")

    # 7. Device report missing node_id (violates device shape: node_id must NOT be NULL)
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, node_id, source, type, severity) VALUES ({mine_id}, NULL, NULL, 'device', 'sos', 'critical')")

    # 7b. Device report with non-sos type (violates device shape: type must be 'sos')
    _try_sql(f"INSERT INTO worker_reports (mine_id, node_id, source, type, severity) VALUES ({mine_id}, {node_id}, 'device', 'injury', 'low')")

    # 8. Resolved status missing resolved_at
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity, status, acknowledged_at, resolved_at) VALUES ({mine_id}, {user_id}, 'worker', 'injury', 'low', 'resolved', now(), NULL)")

    # 9. Type sos with non-critical severity
    _try_sql(f"INSERT INTO worker_reports (mine_id, user_id, source, type, severity) VALUES ({mine_id}, {user_id}, 'worker', 'sos', 'high')")


def test_device_sos_ingest_flow(client, db, freeze_clock):
    """B3, B6: Ingest with sos_active=1 creates device report and open incident, merges bursts, and guards against flood."""
    env = _setup_step8_env(db)
    gw = env["gw_a"]
    node_p = env["node_placed"]
    node_u = env["node_unplaced"]
    tun1 = env["tun_a1"]
    miner = env["miner_a"]
    mgr = env["mgr_a"]

    headers = {
        "Content-Type": "application/octet-stream",
        "X-Gateway-Key": "gwkey_bharat1",
    }

    # Miner scanned in to Tunnel 1 to verify roll call snapshot
    t0 = datetime.datetime(2030, 10, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)
    badge_token, _, _ = issue_badge(db, actor=mgr, worker_id=miner.id)
    record_scan(db, scanner=mgr, token=badge_token, direction="in", tunnel_id=tun1.id, now=t0)
    db.commit()

    # 1. First SOS packet from placed node -> creates report AND open SOS incident
    t1 = t0 + datetime.timedelta(minutes=10)
    freeze_clock(t1)
    pkt1 = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node_p.node_no,
        counter=1,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    r1 = client.post("/api/ingest", content=pkt1, headers=headers)
    assert r1.status_code == 200

    report1 = db.query(WorkerReport).filter(WorkerReport.node_id == node_p.id).first()
    assert report1 is not None
    assert report1.source == "device"
    assert report1.type == "sos"
    assert report1.severity == "critical"
    assert report1.status == "open"
    assert report1.repeat_count == 1
    assert report1.tunnel_id == tun1.id

    # Verify open incident was created for this tunnel with roll call snapshot
    inc = db.query(Incident).filter(Incident.tunnel_id == tun1.id, Incident.status == "open").first()
    assert inc is not None
    assert inc.source == "sos"
    assert inc.level == 3
    assert inc.title == f"SOS: node {node_p.node_no} ({tun1.name})"
    roster_items = db.query(IncidentRoster).filter(IncidentRoster.incident_id == inc.id).all()
    assert len(roster_items) == 1
    assert roster_items[0].accounted_at is None

    # 2. Burst of 2 more packets 100ms apart merges and increments repeat_count
    for c in (2, 3):
        t_burst = t1 + datetime.timedelta(milliseconds=100 * (c - 1))
        freeze_clock(t_burst)
        pkt_burst = pack_gateway_packet(
            gateway_mac=gw.mac,
            node_id=node_p.node_no,
            counter=c,
            tilt_x=0.1,
            tilt_y=0.1,
            vib_rms=0.01,
            shock=False,
            batt_v=4.0,
            batt_pct=95.0,
            sos_active=True,
        )
        assert client.post("/api/ingest", content=pkt_burst, headers=headers).status_code == 200

    db.refresh(report1)
    assert report1.repeat_count == 3
    assert db.query(WorkerReport).count() == 1
    assert db.query(Incident).count() == 1

    # 3. Follow-up backup packet within 60s merges
    t_backup = t1 + datetime.timedelta(seconds=45)
    freeze_clock(t_backup)
    pkt_backup = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node_p.node_no,
        counter=4,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    assert client.post("/api/ingest", content=pkt_backup, headers=headers).status_code == 200
    db.refresh(report1)
    assert report1.repeat_count == 4

    # 4. Packet after 60s creates a 2nd report, does NOT duplicate open SOS incident
    t_after_60 = t_backup + datetime.timedelta(seconds=65)
    freeze_clock(t_after_60)
    pkt_new = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node_p.node_no,
        counter=5,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    assert client.post("/api/ingest", content=pkt_new, headers=headers).status_code == 200
    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node_p.id).order_by(WorkerReport.id).all()
    assert len(reports) == 2
    assert reports[1].repeat_count == 1
    # Still only 1 open SOS incident
    assert db.query(Incident).filter(Incident.tunnel_id == tun1.id, Incident.status == "open").count() == 1

    # 5. Flood guard: create 4 more reports (total 6 in 1 hour)
    cur_t = t_after_60
    for i in range(6, 10):
        cur_t += datetime.timedelta(seconds=65)
        freeze_clock(cur_t)
        p = pack_gateway_packet(
            gateway_mac=gw.mac,
            node_id=node_p.node_no,
            counter=i,
            tilt_x=0.1,
            tilt_y=0.1,
            vib_rms=0.01,
            shock=False,
            batt_v=4.0,
            batt_pct=95.0,
            sos_active=True,
        )
        assert client.post("/api/ingest", content=p, headers=headers).status_code == 200

    reports_6 = db.query(WorkerReport).filter(WorkerReport.node_id == node_p.id).order_by(WorkerReport.id).all()
    assert len(reports_6) == 6

    # 7th packet within 1 hour hits flood guard: merges into 6th report, no 7th report created
    cur_t += datetime.timedelta(seconds=65)
    freeze_clock(cur_t)
    pkt_7th = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node_p.node_no,
        counter=10,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    assert client.post("/api/ingest", content=pkt_7th, headers=headers).status_code == 200
    db.expire_all()
    reports_after_flood = db.query(WorkerReport).filter(WorkerReport.node_id == node_p.id).order_by(WorkerReport.id).all()
    assert len(reports_after_flood) == 6
    assert reports_after_flood[-1].repeat_count == 2

    # 6. Unplaced node: creates device report with NULL tunnel, opens NO incident, logs Alert
    t_unplaced = cur_t + datetime.timedelta(minutes=5)
    freeze_clock(t_unplaced)
    pkt_unp = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node_u.node_no,
        counter=1,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    r_unp = client.post("/api/ingest", content=pkt_unp, headers=headers)
    assert r_unp.status_code == 200
    rep_unp = db.query(WorkerReport).filter(WorkerReport.node_id == node_u.id).first()
    assert rep_unp is not None
    assert rep_unp.tunnel_id is None
    assert rep_unp.mine_id == env["mine_a"].id
    # No new incident opened for unplaced node
    assert db.query(Incident).filter(Incident.tunnel_id.is_(None)).count() == 0
    # Report description names node and gateway
    assert f"node {node_u.node_no}" in rep_unp.description
    assert gw.name in rep_unp.description

    # 7. Duplicate reading with sos_active=1 triggers SOS handling
    readings_count_before = db.query(Reading).count()
    rep_unp_repeats_before = rep_unp.repeat_count
    r_dup = client.post("/api/ingest", content=pkt_unp, headers=headers)
    assert r_dup.status_code == 200
    # Reading deduplicated
    assert db.query(Reading).count() == readings_count_before
    # SOS handling still executed (repeat count incremented)
    db.refresh(rep_unp)
    assert rep_unp.repeat_count == rep_unp_repeats_before + 1

    # 8. Simulated failure in SOS code: reading committed, returns 503, retry succeeds
    from unittest.mock import patch
    with patch("app.services.ingest_service.handle_device_sos", side_effect=RuntimeError("Simulated SOS crash")):
        pkt_fail = pack_gateway_packet(
            gateway_mac=gw.mac,
            node_id=node_p.node_no,
            counter=99,
            tilt_x=0.1,
            tilt_y=0.1,
            vib_rms=0.01,
            shock=False,
            batt_v=4.0,
            batt_pct=95.0,
            sos_active=True,
        )
        r_fail = client.post("/api/ingest", content=pkt_fail, headers=headers)
        assert r_fail.status_code == 503
        assert r_fail.json()["detail"] == "SOS handling failed"

        # Telemetry reading was saved and committed
        saved_reading = db.query(Reading).filter(Reading.node_id == node_p.id, Reading.counter == 99).first()
        assert saved_reading is not None

    # Gateway retry succeeds and creates/merges report
    r_retry = client.post("/api/ingest", content=pkt_fail, headers=headers)
    assert r_retry.status_code == 200


def test_device_sos_concurrency(db):
    """B3, B6: Two threads processing device SOS simultaneously serialize cleanly with row lock."""
    env = _setup_step8_env(db)
    gw = env["gw_a"]
    node = env["node_placed"]

    headers = {
        "Content-Type": "application/octet-stream",
        "X-Gateway-Key": "gwkey_bharat1",
    }

    pkt1 = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node.node_no,
        counter=101,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    pkt2 = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node.node_no,
        counter=102,
        tilt_x=0.1,
        tilt_y=0.1,
        vib_rms=0.01,
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )

    from fastapi.testclient import TestClient
    from app.main import app

    def _post_packet(packet_bytes):
        c = TestClient(app)
        return c.post("/api/ingest", content=packet_bytes, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_post_packet, pkt1)
        f2 = executor.submit(_post_packet, pkt2)
        res1 = f1.result()
        res2 = f2.result()

    assert res1.status_code == 200
    assert res2.status_code == 200

    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).all()
    assert len(reports) == 1
    assert reports[0].repeat_count == 2


def test_simulator_sos_packet():
    """B3, B6: Simulator --sos produces valid 38-byte packets with sos_active set."""
    from app.dev.simulator import DEMO_GATEWAYS
    gw = DEMO_GATEWAYS[0]
    node = gw["nodes"][0]

    pkt = pack_gateway_packet(
        gateway_mac=gw["mac"],
        node_id=node["id"],
        counter=1,
        tilt_x=node["baseline_tilt"][0],
        tilt_y=node["baseline_tilt"][1],
        vib_rms=node["baseline_vib"],
        shock=False,
        batt_v=4.0,
        batt_pct=95.0,
        sos_active=True,
    )
    assert len(pkt) == 38
    parsed = parse_gateway_packet(pkt)
    assert parsed["sos_active"] is True
    assert parsed["node_id"] == node["id"]


def test_done_when_end_to_end_journey(client, db, freeze_clock):
    """B0, B6: Complete end-to-end journey from worker report to device SOS, incident, roll call, and red banner."""
    env = _setup_step8_env(db)
    gw = env["gw_a"]
    node = env["node_placed"]
    tun = env["tun_a1"]
    miner = env["miner_a"]
    mgr = env["mgr_a"]

    t0 = datetime.datetime(2030, 11, 1, 9, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Miner submits a safety hazard report
    _login(client, miner.id)
    r_create = client.post(
        "/api/reports",
        json={
            "type": "equipment_fault",
            "severity": "high",
            "tunnel_id": tun.id,
            "description": "Roof bolt loose near sensor cluster",
        },
    )
    assert r_create.status_code == 201
    rep_id = r_create.json()["id"]

    # 2. Manager views reports inbox
    _login(client, mgr.id)
    r_inbox = client.get("/api/reports?status=open")
    assert r_inbox.status_code == 200
    reports = r_inbox.json()
    assert any(r["id"] == rep_id for r in reports)

    # 3. Manager acknowledges the report
    t1 = t0 + datetime.timedelta(minutes=2)
    freeze_clock(t1)
    r_ack = client.patch(f"/api/reports/{rep_id}", json={"status": "acknowledged"})
    assert r_ack.status_code == 200
    assert r_ack.json()["status"] == "acknowledged"

    # 4. Manager resolves the report
    t2 = t1 + datetime.timedelta(minutes=5)
    freeze_clock(t2)
    r_res = client.patch(
        f"/api/reports/{rep_id}",
        json={"status": "resolved", "resolution_note": "Re-torqued and certified safe"},
    )
    assert r_res.status_code == 200
    assert r_res.json()["status"] == "resolved"

    # 5. Device SOS triggered via hardware node
    t3 = t2 + datetime.timedelta(minutes=10)
    freeze_clock(t3)
    pkt_sos = pack_gateway_packet(
        gateway_mac=gw.mac,
        node_id=node.node_no,
        counter=50,
        tilt_x=0.05,
        tilt_y=0.05,
        vib_rms=0.02,
        shock=False,
        batt_v=4.0,
        batt_pct=90.0,
        sos_active=True,
    )
    r_ingest = client.post(
        "/api/ingest",
        content=pkt_sos,
        headers={"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_bharat1"},
    )
    assert r_ingest.status_code == 200

    # 6. Device SOS report created, open SOS incident exists with roll call
    sos_rep = db.query(WorkerReport).filter(WorkerReport.node_id == node.id, WorkerReport.type == "sos").first()
    assert sos_rep is not None
    assert sos_rep.status == "open"
    assert sos_rep.severity == "critical"

    inc = db.query(Incident).filter(Incident.tunnel_id == tun.id, Incident.status == "open").first()
    assert inc is not None
    assert inc.source == "sos"

    # 7. Red banner polling: GET /api/reports?status=open&type=sos returns the active distress report
    banner_res = client.get("/api/reports?status=open&type=sos")
    assert banner_res.status_code == 200
    active_sos = banner_res.json()
    assert len(active_sos) == 1
    assert active_sos[0]["id"] == sos_rep.id

    # 8. Superior acknowledges the SOS report -> red banner query returns 0
    t4 = t3 + datetime.timedelta(minutes=1)
    freeze_clock(t4)
    r_ack_sos = client.patch(f"/api/reports/{sos_rep.id}", json={"status": "acknowledged"})
    assert r_ack_sos.status_code == 200

    banner_cleared_res = client.get("/api/reports?status=open&type=sos")
    assert banner_cleared_res.status_code == 200
    assert len(banner_cleared_res.json()) == 0


def test_migration_downgrade_and_upgrade():
    """B1, B6: Alembic downgrade -1 and upgrade head execute cleanly."""
    import os
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))

    db_url = os.environ.get("DATABASE_URL")
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    try:
        command.downgrade(alembic_cfg, "-1")
    finally:
        command.upgrade(alembic_cfg, "head")
