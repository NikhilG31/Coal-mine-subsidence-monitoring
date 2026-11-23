"""Step 8 follow-up tests: SOS domain resilience, merge lifecycle, and ingest contract."""
import concurrent.futures, datetime, threading
from typing import Any, Dict
from unittest.mock import patch
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.database import SessionLocal

from app.main import app
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.mine import Mine
from app.models.node import Node
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_report import WorkerReport
from app.security.passwords import hash_password
from app.security.roles import ROLE_MANAGER
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.gateway_keys import hash_key
from app.services.packet_codec import pack_gateway_packet
from app.services.sos_service import ensure_sos_incident


def _setup_env(db: Session) -> Dict[str, Any]:
    comp = Company(name="Test SOS Co", name_normalized="test sos co")
    db.add(comp)
    db.flush()
    mine = Mine(company_id=comp.id, name="Test SOS Mine", status="active", method="underground")
    db.add(mine)
    db.flush()
    tun = Tunnel(mine_id=mine.id, name="Tunnel 1", code="T1")
    gw = Gateway(mac="10:20:30:40:50:60", mine_id=mine.id, name="Test Gateway", api_key_hash=hash_key("gwkey_test"), active=True)
    db.add_all([tun, gw])
    db.flush()
    node_p = Node(gateway_id=gw.id, node_no=10, tunnel_id=tun.id, lat=23.75, lon=86.42, status="normal")
    node_u = Node(gateway_id=gw.id, node_no=20, tunnel_id=None, lat=None, lon=None, status="normal")
    mgr = User(company_id=comp.id, username="mgr_test", full_name="Manager Test", password_hash=hash_password("Pass123"), role=ROLE_MANAGER, active=True)
    db.add_all([node_p, node_u, mgr])
    db.flush()
    db.add(UserMine(user_id=mgr.id, mine_id=mine.id))
    db.commit()
    return {"comp": comp, "mine": mine, "tun": tun, "gw": gw, "node_placed": node_p, "node_unplaced": node_u, "mgr": mgr}


def _pkt(gw_mac: str, node_no: int, counter: int, sos: bool = True) -> bytes:
    return pack_gateway_packet(gw_mac, node_no, counter, 0.1, 0.1, 0.01, False, 4.0, 95.0, sos)


def test_signal_merged_into_acknowledged_report(client, db, freeze_clock):
    """F3, F5: Signal merges into an acknowledged report and keeps acknowledged status."""
    env = _setup_env(db)
    gw, node, mgr = env["gw"], env["node_placed"], env["mgr"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    t0 = datetime.datetime(2030, 3, 1, 9, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 601), headers=headers).status_code == 200
    rep = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).first()
    assert rep.status == "open"

    t_ack = t0 + datetime.timedelta(seconds=10)
    freeze_clock(t_ack)
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))
    assert client.patch(f"/api/reports/{rep.id}", json={"status": "acknowledged"}).status_code == 200
    db.refresh(rep)
    assert rep.status == "acknowledged"

    t_merge = t0 + datetime.timedelta(seconds=35)
    freeze_clock(t_merge)
    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 602), headers=headers).status_code == 200

    assert db.query(WorkerReport).filter(WorkerReport.node_id == node.id).count() == 1
    db.refresh(rep)
    assert rep.repeat_count == 2 and rep.status == "acknowledged" and rep.last_signal_at == t_merge


def test_signal_after_report_resolved_creates_new_report(client, db, freeze_clock):
    """F3, F5: Signal after report is resolved creates a new report (never merges into resolved)."""
    env = _setup_env(db)
    gw, node, mgr = env["gw"], env["node_placed"], env["mgr"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    t0 = datetime.datetime(2030, 3, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 701), headers=headers).status_code == 200
    rep1 = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).first()

    t_res = t0 + datetime.timedelta(seconds=10)
    freeze_clock(t_res)
    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))
    client.patch(f"/api/reports/{rep1.id}", json={"status": "resolved", "resolution_note": "Fixed"})
    db.refresh(rep1)
    assert rep1.status == "resolved"

    t_new = t0 + datetime.timedelta(seconds=20)
    freeze_clock(t_new)
    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 702), headers=headers).status_code == 200

    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).order_by(WorkerReport.id).all()
    assert len(reports) == 2
    assert reports[0].id == rep1.id and reports[0].status == "resolved"
    assert reports[1].status == "open" and reports[1].repeat_count == 1 and reports[1].created_at == t_new


def test_flood_guard_merges_only_unresolved(client, db, freeze_clock):
    """F3, F5: Flood guard absorbs signals only when newest report is unresolved; resolved report never absorbs."""
    env = _setup_env(db)
    gw, node, mgr = env["gw"], env["node_placed"], env["mgr"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    cur_t = datetime.datetime(2030, 4, 1, 9, 0, tzinfo=datetime.timezone.utc)

    for i in range(1, 7):
        freeze_clock(cur_t)
        assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 800 + i), headers=headers).status_code == 200
        cur_t += datetime.timedelta(seconds=65)

    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).order_by(WorkerReport.id).all()
    assert len(reports) == 6 and reports[5].status == "open"
    rep6 = reports[5]

    freeze_clock(cur_t)
    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 807), headers=headers).status_code == 200
    assert db.query(WorkerReport).filter(WorkerReport.node_id == node.id).count() == 6
    db.refresh(rep6)
    assert rep6.repeat_count == 2

    client.cookies.set(COOKIE_NAME, create_session_token(mgr.id))
    client.patch(f"/api/reports/{rep6.id}", json={"status": "resolved", "resolution_note": "Closed"})
    db.refresh(rep6)
    assert rep6.status == "resolved"

    cur_t += datetime.timedelta(seconds=65)
    freeze_clock(cur_t)
    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 808), headers=headers).status_code == 200
    reports_after = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).order_by(WorkerReport.id).all()
    assert len(reports_after) == 7 and reports_after[-1].status == "open" and reports_after[-1].repeat_count == 1


def test_open_incident_failure_contract_and_retry(client, db):
    """F1, F4, F5: open_incident failure -> 503, reading stored, retry yields 1 report + 1 incident."""
    env = _setup_env(db)
    gw, node = env["gw"], env["node_placed"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    pkt = _pkt(gw.mac, node.node_no, 301)

    with patch("app.services.sos_service.open_incident", side_effect=RuntimeError("Simulated incident failure")):
        resp_fail = client.post("/api/ingest", content=pkt, headers=headers)
        assert resp_fail.status_code == 503 and resp_fail.json() == {"detail": "SOS handling failed"}
        reading = db.query(Reading).filter(Reading.node_id == node.id, Reading.counter == 301).first()
        assert reading is not None
        assert db.query(WorkerReport).filter(WorkerReport.node_id == node.id).count() == 0
        assert db.query(Incident).count() == 0

    assert client.post("/api/ingest", content=pkt, headers=headers).status_code == 200
    assert db.query(Reading).filter(Reading.node_id == node.id, Reading.counter == 301).count() == 1
    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).all()
    assert len(reports) == 1 and reports[0].repeat_count == 1 and reports[0].incident_id is not None
    incidents = db.query(Incident).filter(Incident.tunnel_id == node.tunnel_id, Incident.status == "open").all()
    assert len(incidents) == 1 and reports[0].incident_id == incidents[0].id


def test_merged_signal_creates_missing_incident(client, db, freeze_clock):
    """F2, F5: A merged signal creates a missing incident if earlier attempt lacked one."""
    env = _setup_env(db)
    gw, node, tun = env["gw"], env["node_placed"], env["tun"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    t0 = datetime.datetime(2030, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    rep = WorkerReport(
        mine_id=gw.mine_id, tunnel_id=tun.id, node_id=node.id, source="device",
        type="sos", severity="critical", status="open", repeat_count=1,
        created_at=t0, last_signal_at=t0, incident_id=None,
    )
    db.add(rep)
    db.commit()
    assert db.query(Incident).count() == 0

    t1 = t0 + datetime.timedelta(seconds=20)
    freeze_clock(t1)
    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 401), headers=headers).status_code == 200
    db.refresh(rep)
    assert rep.repeat_count == 2 and rep.incident_id is not None
    inc = db.query(Incident).filter(Incident.id == rep.incident_id).first()
    assert inc is not None and inc.source == "sos" and inc.status == "open"


def test_unique_index_race_reuses_existing_incident(db):
    """F1, F5: Unique-index conflict on uq_incidents_open_sos is caught and reuses open incident."""
    env = _setup_env(db)
    gw, node, tun = env["gw"], env["node_placed"], env["tun"]
    now = datetime.datetime(2030, 1, 1, 12, 0, tzinfo=datetime.timezone.utc)

    report = WorkerReport(
        mine_id=gw.mine_id, tunnel_id=tun.id, node_id=node.id, source="device",
        type="sos", severity="critical", status="open", created_at=now, last_signal_at=now, repeat_count=1,
    )
    report2 = WorkerReport(
        mine_id=gw.mine_id, tunnel_id=tun.id, node_id=node.id, source="device",
        type="sos", severity="critical", status="open", created_at=now, last_signal_at=now, repeat_count=1,
    )
    db.add(report)
    db.flush()

    def _race_open(*args, **kwargs):
        with SessionLocal() as s2:
            other_inc = Incident(mine_id=gw.mine_id, tunnel_id=tun.id, source="sos", level=3, title="SOS: node 10 (T1)", status="open", opened_at=now)
            s2.add(other_inc)
            s2.commit()
        raise IntegrityError("duplicate key violates uq_incidents_open_sos", params=None, orig=Exception("uq_incidents_open_sos"))

    with patch("app.services.sos_service.open_incident", side_effect=_race_open):
        inc = ensure_sos_incident(db, node, gw, report, now)
        assert inc is not None and inc.source == "sos" and inc.status == "open" and report.incident_id == inc.id

    inc.status, inc.closed_at = "closed", now
    db.add(report2)
    db.flush()

    with patch(
        "app.services.sos_service.open_incident",
        side_effect=IntegrityError("violates foreign key", params=None, orig=Exception("fk_viol")),
    ):
        with pytest.raises(IntegrityError):
            ensure_sos_incident(db, node, gw, report2, now)


def test_sos_on_duplicate_reading_creates_or_merges(client, db, freeze_clock):
    """F4, F5: SOS handling runs even when reading is duplicate, creating or merging report."""
    env = _setup_env(db)
    gw, node = env["gw"], env["node_placed"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    t0 = datetime.datetime(2030, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    assert client.post("/api/ingest", content=_pkt(gw.mac, node.node_no, 500, False), headers=headers).status_code == 200
    assert db.query(Reading).filter(Reading.node_id == node.id, Reading.counter == 500).count() == 1
    assert db.query(WorkerReport).filter(WorkerReport.node_id == node.id).count() == 0

    t_dup1 = t0 + datetime.timedelta(seconds=1)
    freeze_clock(t_dup1)
    pkt_sos = _pkt(gw.mac, node.node_no, 500, True)
    assert client.post("/api/ingest", content=pkt_sos, headers=headers).status_code == 200
    assert db.query(Reading).filter(Reading.node_id == node.id, Reading.counter == 500).count() == 1
    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).all()
    assert len(reports) == 1 and reports[0].repeat_count == 1

    t_dup2 = t0 + datetime.timedelta(seconds=2)
    freeze_clock(t_dup2)
    assert client.post("/api/ingest", content=pkt_sos, headers=headers).status_code == 200
    db.refresh(reports[0])
    assert reports[0].repeat_count == 2


def test_concurrent_signals_barrier(db):
    """F5: Two concurrent signals (two sessions, barrier) -> exactly one report."""
    env = _setup_env(db)
    gw, node = env["gw"], env["node_placed"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    pkt1 = _pkt(gw.mac, node.node_no, 201)
    pkt2 = _pkt(gw.mac, node.node_no, 202)

    barrier = threading.Barrier(2)

    def _worker(pkt):
        c = TestClient(app)
        barrier.wait()
        return c.post("/api/ingest", content=pkt, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1, f2 = executor.submit(_worker, pkt1), executor.submit(_worker, pkt2)
        r1, r2 = f1.result(), f2.result()

    assert r1.status_code == 200 and r2.status_code == 200
    reports = db.query(WorkerReport).filter(WorkerReport.node_id == node.id).all()
    assert len(reports) == 1 and reports[0].repeat_count == 2


def test_unplaced_node_report_no_incident(client, db, freeze_clock):
    """F2, F5: Unplaced node creates worker report but opens NO incident."""
    env = _setup_env(db)
    gw, node_u = env["gw"], env["node_unplaced"]
    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gwkey_test"}
    now = datetime.datetime(2030, 5, 1, 12, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    assert client.post("/api/ingest", content=_pkt(gw.mac, node_u.node_no, 901), headers=headers).status_code == 200
    report = db.query(WorkerReport).filter(WorkerReport.node_id == node_u.id).first()
    assert report is not None and report.source == "device" and report.type == "sos" and report.severity == "critical"
    assert report.tunnel_id is None and report.incident_id is None
    assert f"node {node_u.node_no}" in report.description and gw.name in report.description
    assert db.query(Incident).count() == 0
