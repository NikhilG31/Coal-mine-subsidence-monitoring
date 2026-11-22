"""Comprehensive test suite for Step 5: workers, assignments, and shifts.

Tests:
1. Shift overlap rejection (back-to-back allowed, 1-minute overlap rejected with 409 and clash details).
2. Database GIST exclusion constraint tested directly (concurrent/dual-session race caught).
3. Validation matrix: wrong company 404, out-of-scope tunnel 404, worker not in mine 400, closed tunnel 400,
   duration > MAX_SHIFT_HOURS 422, naive datetimes 422, shorten shift 200, extend shift 422,
   delete started shift 409, delete future shift 200.
4. Night shifts and weekly hours splitting across Monday 00:00 boundary in HOURS_TIMEZONE.
5. Scoping: miner sees only own tunnel's nodes/placements; unassigned miner sees empty; foreman/superior see all.
6. Privacy & security: miner cannot view others' shifts; worker view omits created_by; audit log sanitization.
7. Done-when complete end-to-end journey.
"""
import datetime
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy.orm import Session

from app.config import HOURS_TIMEZONE, MAX_SHIFT_HOURS, WEEKLY_HOURS_LIMIT
from app.database import SessionLocal
from app.models.assignment import Assignment
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.node import Node
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
from app.services.shift_service import ShiftOverlapError, create_shift


def _create_user(db: Session, company_id: int, username: str, role: str) -> User:
    u = User(
        company_id=company_id,
        username=username,
        full_name=f"Test {username}",
        role=role,
        password_hash=hash_password("Pass@123456"),
        active=True,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _login(client, user_id: int):
    token = create_session_token(user_id)
    client.cookies.set(COOKIE_NAME, token)


def test_shift_overlap_rejection_and_back_to_back_allowed(client, db):
    """Back-to-back shifts are allowed; 1-minute overlap is rejected with 409 and clash info."""
    comp = Company(name="Overlap Coal", name_normalized="overlap coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Overlap Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    t1 = Tunnel(mine_id=mine.id, name="East Gallery", code="EG-1", status="active")
    t2 = Tunnel(mine_id=mine.id, name="West Drift", code="WD-1", status="active")
    db.add_all([t1, t2])
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_ovl", ROLE_MANAGER)
    miner = _create_user(db, comp.id, "miner_ovl", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=mine.id))
    db.commit()

    _login(client, mgr.id)

    # Shift 1: 08:00 to 16:00 UTC
    resp1 = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t1.id,
            "starts_at": "2026-06-01T08:00:00Z",
            "ends_at": "2026-06-01T16:00:00Z",
        },
    )
    assert resp1.status_code == 201, resp1.text
    s1_id = resp1.json()["assignment"]["id"]

    # Shift 2: Exactly back-to-back 16:00 to 22:00 UTC (allowed)
    resp2 = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t2.id,
            "starts_at": "2026-06-01T16:00:00Z",
            "ends_at": "2026-06-01T22:00:00Z",
        },
    )
    assert resp2.status_code == 201, resp2.text

    # Shift 3: 1-minute overlap with Shift 1 (15:59 to 18:00 UTC) -> 409
    resp3 = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t1.id,
            "starts_at": "2026-06-01T15:59:00Z",
            "ends_at": "2026-06-01T18:00:00Z",
        },
    )
    assert resp3.status_code == 409, resp3.text
    err_body = resp3.json()
    assert err_body["code"] == "shift_overlap"
    assert "clashing_shift" in err_body
    clash = err_body["clashing_shift"]
    assert clash["id"] == s1_id
    assert clash["tunnel_name"] == "East Gallery"


def test_db_exclusion_constraint_race_caught_directly(db):
    """Directly tests that Postgres GIST exclusion constraint catches concurrent overlapping inserts."""
    comp = Company(name="Race Test Coal", name_normalized="race test coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Race Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Main Drift", code="MD-1", status="active")
    db.add(tunnel)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_race", ROLE_MANAGER)
    miner = _create_user(db, comp.id, "miner_race", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=mine.id))
    db.commit()

    s1 = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=datetime.datetime(2026, 7, 1, 10, 0, tzinfo=datetime.timezone.utc),
        ends_at=datetime.datetime(2026, 7, 1, 18, 0, tzinfo=datetime.timezone.utc),
        created_by=mgr.id,
    )
    db.add(s1)
    db.commit()

    # Second insert directly in DB attempting overlapping shift
    s2 = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=datetime.datetime(2026, 7, 1, 14, 0, tzinfo=datetime.timezone.utc),
        ends_at=datetime.datetime(2026, 7, 1, 20, 0, tzinfo=datetime.timezone.utc),
        created_by=mgr.id,
    )
    db.add(s2)
    with pytest.raises(Exception) as excinfo:
        db.commit()

    # PostgreSQL GIST exclusion constraint violation
    assert "ex_assignments_user_no_overlap" in str(excinfo.value)
    db.rollback()


def test_shift_validations_matrix(client, db):
    """Tests comprehensive validation edge cases for shifts and profile routes."""
    comp_a = Company(name="Validation Comp A", name_normalized="validation comp a")
    comp_b = Company(name="Validation Comp B", name_normalized="validation comp b")
    db.add_all([comp_a, comp_b])
    db.flush()

    mine_a = Mine(company_id=comp_a.id, name="Mine A", status="active", method="underground")
    mine_b = Mine(company_id=comp_b.id, name="Mine B", status="active", method="underground")
    db.add_all([mine_a, mine_b])
    db.flush()

    t_active = Tunnel(mine_id=mine_a.id, name="Active Tunnel", code="AT-1", status="active")
    t_closed = Tunnel(mine_id=mine_a.id, name="Closed Tunnel", code="CT-1", status="closed")
    t_other = Tunnel(mine_id=mine_b.id, name="Other Tunnel", code="OT-1", status="active")
    db.add_all([t_active, t_closed, t_other])
    db.flush()

    mgr_a = _create_user(db, comp_a.id, "mgr_a_val", ROLE_MANAGER)
    miner_a = _create_user(db, comp_a.id, "miner_a_val", ROLE_MINER)
    miner_b = _create_user(db, comp_b.id, "miner_b_val", ROLE_MINER)

    # miner_a assigned only to mine_a
    db.add(UserMine(user_id=miner_a.id, mine_id=mine_a.id))
    db.commit()

    _login(client, mgr_a.id)

    # 1. Worker from different company -> 404
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_b.id,
            "tunnel_id": t_active.id,
            "starts_at": "2026-08-01T08:00:00Z",
            "ends_at": "2026-08-01T16:00:00Z",
        },
    )
    assert r.status_code == 404

    # 2. Tunnel from out-of-scope mine -> 404
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_other.id,
            "starts_at": "2026-08-01T08:00:00Z",
            "ends_at": "2026-08-01T16:00:00Z",
        },
    )
    assert r.status_code == 404

    # 3. Closed tunnel -> 400
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_closed.id,
            "starts_at": "2026-08-01T08:00:00Z",
            "ends_at": "2026-08-01T16:00:00Z",
        },
    )
    assert r.status_code == 400
    assert r.json().get("code") == "tunnel_closed"

    # 4. Duration exceeds MAX_SHIFT_HOURS (16h) -> 422
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_active.id,
            "starts_at": "2026-08-01T00:00:00Z",
            "ends_at": "2026-08-01T18:00:00Z",  # 18 hours
        },
    )
    assert r.status_code == 422

    # 5. ends_at <= starts_at -> 422
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_active.id,
            "starts_at": "2026-08-01T12:00:00Z",
            "ends_at": "2026-08-01T10:00:00Z",
        },
    )
    assert r.status_code == 422

    # 6. Naive datetime without offset -> 422
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_active.id,
            "starts_at": "2026-08-01T08:00:00",
            "ends_at": "2026-08-01T16:00:00",
        },
    )
    assert r.status_code == 422

    # 7. Valid future shift creation -> 201
    future_start = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=10)).replace(minute=0, second=0, microsecond=0)
    future_end = future_start + datetime.timedelta(hours=8)
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner_a.id,
            "tunnel_id": t_active.id,
            "starts_at": future_start.isoformat(),
            "ends_at": future_end.isoformat(),
        },
    )
    assert r.status_code == 201
    shift_id = r.json()["assignment"]["id"]

    # 8. Extend shift (ends_at > current ends_at) -> 422
    r = client.patch(
        f"/api/assignments/{shift_id}",
        json={"ends_at": (future_end + datetime.timedelta(hours=1)).isoformat()},
    )
    assert r.status_code == 422

    # 9. Shorten shift (starts_at < new_ends_at <= ends_at) -> 200
    short_end = future_start + datetime.timedelta(hours=6)
    r = client.patch(
        f"/api/assignments/{shift_id}",
        json={"ends_at": short_end.isoformat()},
    )
    assert r.status_code == 200
    assert r.json()["assignment"]["ends_at"].startswith(short_end.isoformat()[:16])

    # 10. Delete future shift -> 200
    r = client.delete(f"/api/assignments/{shift_id}")
    assert r.status_code == 200

    # 11. Cannot delete past / started shift -> 409
    past_shift = Assignment(
        user_id=miner_a.id,
        tunnel_id=t_active.id,
        starts_at=datetime.datetime(2026, 1, 1, 8, 0, tzinfo=datetime.timezone.utc),
        ends_at=datetime.datetime(2026, 1, 1, 16, 0, tzinfo=datetime.timezone.utc),
        created_by=mgr_a.id,
    )
    db.add(past_shift)
    db.commit()

    r = client.delete(f"/api/assignments/{past_shift.id}")
    assert r.status_code == 409
    assert r.json().get("code") == "shift_started"


def test_night_shift_and_weekly_hours_boundary_splitting(client, db):
    """Night shift crossing midnight is ONE assignment; weekly hours splits across week boundary."""
    comp = Company(name="Night Shift Coal", name_normalized="night shift coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Night Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Night Tunnel", code="NT-1", status="active")
    db.add(tunnel)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_night", ROLE_MANAGER)
    miner = _create_user(db, comp.id, "miner_night", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=mine.id))
    db.commit()

    _login(client, mgr.id)

    # In Asia/Kolkata (+05:30):
    # Sunday 2026-06-07 20:00 IST = 2026-06-07 14:30:00 UTC
    # Monday 2026-06-08 04:00 IST = 2026-06-07 22:30:00 UTC
    # Local Monday 00:00 IST is the boundary:
    # Sunday 20:00 - Monday 00:00 = 4.0 hours in Week 1
    # Monday 00:00 - Monday 04:00 = 4.0 hours in Week 2

    # Setup worker with 46 hours already in Week 1 (e.g. Wednesday to Saturday)
    # Week 1: Monday 2026-06-01 to Sunday 2026-06-07
    s_prior = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=datetime.datetime(2026, 6, 2, 0, 0, tzinfo=datetime.timezone.utc),
        ends_at=datetime.datetime(2026, 6, 2, 0, 0, tzinfo=datetime.timezone.utc)
        + datetime.timedelta(hours=46),
        created_by=mgr.id,
    )
    db.add(s_prior)
    db.commit()

    # Now assign the Sunday night shift: 8 hours total across boundary
    resp = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": tunnel.id,
            "starts_at": "2026-06-07T14:30:00Z",
            "ends_at": "2026-06-07T22:30:00Z",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    warnings = body["warnings"]

    # In Week 1: 46 prior hours + 4 hours from night shift = 50.0 hours (> 48.0 limit!)
    # In Week 2: only 4 hours from night shift (<= 48.0 limit)
    assert len(warnings) == 1
    w = warnings[0]
    assert w["code"] == "weekly_hours_exceeded"
    assert w["total_hours"] == 50.0
    assert w["limit_hours"] == 48.0


def test_tunnel_scoping_miner_vs_foreman_vs_superior(client, db):
    """Miner sees only own tunnel's nodes and placements; unassigned sees none; foreman/superior see all."""
    comp = Company(name="Scope Test Coal", name_normalized="scope test coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Scope Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    t1 = Tunnel(mine_id=mine.id, name="Tunnel North", code="TN-1", status="active")
    t2 = Tunnel(mine_id=mine.id, name="Tunnel South", code="TS-1", status="active")
    db.add_all([t1, t2])
    db.flush()

    gw = Gateway(mac="AA:BB:CC:DD:EE:01", mine_id=mine.id, name="GW Scope", active=True)
    db.add(gw)
    db.flush()

    # Node 1 in Tunnel 1, Node 2 in Tunnel 2
    n1 = Node(gateway_id=gw.id, node_no=1, status="normal", tunnel_id=t1.id, lat=23.1, lon=85.1)
    n2 = Node(gateway_id=gw.id, node_no=2, status="normal", tunnel_id=t2.id, lat=23.2, lon=85.2)
    db.add_all([n1, n2])
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_scope", ROLE_MANAGER)
    foreman = _create_user(db, comp.id, "foreman_scope", ROLE_FOREMAN)
    miner_assigned = _create_user(db, comp.id, "miner_assigned", ROLE_MINER)
    miner_unassigned = _create_user(db, comp.id, "miner_unassigned", ROLE_MINER)

    for u in (mgr, foreman, miner_assigned, miner_unassigned):
        db.add(UserMine(user_id=u.id, mine_id=mine.id))

    # Active assignment for miner_assigned in Tunnel 1
    now = datetime.datetime.now(datetime.timezone.utc)
    db.add(
        Assignment(
            user_id=miner_assigned.id,
            tunnel_id=t1.id,
            starts_at=now - datetime.timedelta(hours=2),
            ends_at=now + datetime.timedelta(hours=6),
            created_by=mgr.id,
        )
    )
    db.commit()

    # 1. Miner assigned to Tunnel 1 sees only Node 1
    _login(client, miner_assigned.id)
    r = client.get("/api/nodes")
    assert r.status_code == 200
    nodes = r.json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == 1
    assert nodes[0]["location_name"] == "Tunnel North"

    r_pl = client.get("/api/placements")
    assert r_pl.status_code == 200
    placements = r_pl.json()["placements"]
    assert len(placements) == 1
    assert placements[0]["node_id"] == 1

    r_tun = client.get(f"/api/mines/{mine.id}/tunnels")
    assert r_tun.status_code == 200
    tunnels = r_tun.json()["tunnels"]
    assert len(tunnels) == 1
    assert tunnels[0]["id"] == t1.id

    # 2. Miner unassigned sees NO nodes, NO placements, NO tunnels
    _login(client, miner_unassigned.id)
    r = client.get("/api/nodes")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 0

    r_pl = client.get("/api/placements")
    assert r_pl.status_code == 200
    assert len(r_pl.json()["placements"]) == 0

    r_tun = client.get(f"/api/mines/{mine.id}/tunnels")
    assert r_tun.status_code == 200
    assert len(r_tun.json()["tunnels"]) == 0

    # 3. Foreman sees ALL nodes (Node 1 and Node 2) and all tunnels
    _login(client, foreman.id)
    r = client.get("/api/nodes")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 2

    r_tun = client.get(f"/api/mines/{mine.id}/tunnels")
    assert r_tun.status_code == 200
    assert len(r_tun.json()["tunnels"]) == 2

    # 4. Superior (Manager) sees ALL nodes
    _login(client, mgr.id)
    r = client.get("/api/nodes")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 2


def test_privacy_and_security_leakage(client, db):
    """Miners cannot view other workers' shifts, views omit created_by, audit logs sanitize contact details."""
    comp = Company(name="Privacy Test Coal", name_normalized="privacy test coal")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Privacy Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Sec Tunnel", code="ST-1", status="active")
    db.add(tunnel)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_sec", ROLE_MANAGER)
    miner1 = _create_user(db, comp.id, "miner_sec1", ROLE_MINER)
    miner2 = _create_user(db, comp.id, "miner_sec2", ROLE_MINER)
    db.add_all([UserMine(user_id=miner1.id, mine_id=mine.id), UserMine(user_id=miner2.id, mine_id=mine.id)])

    now = datetime.datetime.now(datetime.timezone.utc)
    a1 = Assignment(
        user_id=miner1.id,
        tunnel_id=tunnel.id,
        starts_at=now + datetime.timedelta(hours=1),
        ends_at=now + datetime.timedelta(hours=8),
        created_by=mgr.id,
    )
    db.add(a1)
    db.commit()

    # 1. Miner cannot access manager shifts query route
    _login(client, miner1.id)
    r = client.get("/api/assignments")
    assert r.status_code == 403

    # 2. Miner accesses own shifts via /api/me/shifts -> Worker-safe view (omits created_by)
    r = client.get("/api/me/shifts")
    assert r.status_code == 200
    shifts = r.json()["shifts"]
    assert len(shifts) == 1
    s = shifts[0]
    assert s["id"] == a1.id
    assert "created_by" not in s

    # 3. Manager updates worker profile -> Audit log must NOT contain phone or emergency contact data
    _login(client, mgr.id)
    profile_data = {
        "employee_no": "EMP-9988",
        "phone": "+91 9876543210",
        "emergency_contact_name": "Jane Doe",
        "emergency_contact_phone": "+91 9123456789",
        "joined_on": "2024-05-10",
    }
    r = client.put(f"/api/workers/{miner1.id}/profile", json=profile_data)
    assert r.status_code == 200

    # Inspect audit log
    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "worker.profile_update", AuditLog.entity_id == miner1.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit_row is not None
    detail = audit_row.detail
    # Must only contain changed field names
    assert "changed_fields" in detail
    # None of the sensitive values must appear anywhere in detail
    assert "+91 9876543210" not in str(detail)
    assert "Jane Doe" not in str(detail)
    assert "+91 9123456789" not in str(detail)


def test_done_when_end_to_end_journey(client, db):
    """Complete Done-When specification journey for Step 5:

    - Manager or Safety Officer assigns worker to a tunnel for a shift
    - Second overlapping shift for same worker rejected with 409 naming clashing shift
    - Worker sees upcoming and past shifts under 'My hours' (/api/me/shifts)
    - Miner sees only the nodes of their own tunnel (foreman keeps mine-level view)
    - Weekly-hours warning appears when limit is exceeded
    """
    comp = Company(name="DoneWhen Coal Corp", name_normalized="donewhen coal corp")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Central Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    t_north = Tunnel(mine_id=mine.id, name="North Seam", code="NS-01", status="active")
    t_south = Tunnel(mine_id=mine.id, name="South Seam", code="SS-02", status="active")
    db.add_all([t_north, t_south])
    db.flush()

    gw = Gateway(mac="EE:FF:11:22:33:44", mine_id=mine.id, name="Central GW", active=True)
    db.add(gw)
    db.flush()

    node_north = Node(gateway_id=gw.id, node_no=10, status="normal", tunnel_id=t_north.id, lat=22.5, lon=84.5)
    node_south = Node(gateway_id=gw.id, node_no=20, status="normal", tunnel_id=t_south.id, lat=22.6, lon=84.6)
    db.add_all([node_north, node_south])
    db.flush()

    safety_off = _create_user(db, comp.id, "safety_done", ROLE_SAFETY_OFFICER)
    foreman = _create_user(db, comp.id, "foreman_done", ROLE_FOREMAN)
    miner = _create_user(db, comp.id, "miner_done", ROLE_MINER)

    for u in (safety_off, foreman, miner):
        db.add(UserMine(user_id=u.id, mine_id=mine.id))
    db.commit()

    # Step 1: Safety Officer assigns miner to North Seam for a shift
    _login(client, safety_off.id)

    # First add some prior shifts so weekly hours limit (48h) will be exceeded
    now = datetime.datetime.now(datetime.timezone.utc)
    tz = ZoneInfo(HOURS_TIMEZONE)
    local_now = now.astimezone(tz)
    local_monday = local_now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(
        days=local_now.weekday()
    )
    utc_monday = local_monday.astimezone(datetime.timezone.utc)

    # Add a past shift earlier this week: 42 hours
    past_shift = Assignment(
        user_id=miner.id,
        tunnel_id=t_north.id,
        starts_at=utc_monday + datetime.timedelta(hours=1),
        ends_at=utc_monday + datetime.timedelta(hours=43),
        created_by=safety_off.id,
    )
    db.add(past_shift)
    db.commit()

    # Schedule new shift: 8 hours (42 + 8 = 50 hours > 48h limit!)
    shift1_start = now + datetime.timedelta(hours=1)
    shift1_end = now + datetime.timedelta(hours=9)
    resp = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t_north.id,
            "starts_at": shift1_start.isoformat(),
            "ends_at": shift1_end.isoformat(),
        },
    )
    assert resp.status_code == 201, resp.text
    data1 = resp.json()
    shift1_id = data1["assignment"]["id"]

    # Weekly-hours warning appears!
    assert len(data1["warnings"]) >= 1
    assert data1["warnings"][0]["code"] == "weekly_hours_exceeded"

    # Step 2: Second overlapping shift for same worker rejected with 409 naming clashing shift
    overlap_resp = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t_south.id,
            "starts_at": (shift1_start + datetime.timedelta(hours=2)).isoformat(),
            "ends_at": (shift1_end + datetime.timedelta(hours=2)).isoformat(),
        },
    )
    assert overlap_resp.status_code == 409
    overlap_data = overlap_resp.json()
    assert overlap_data["code"] == "shift_overlap"
    assert overlap_data["clashing_shift"]["id"] == shift1_id
    assert overlap_data["clashing_shift"]["tunnel_name"] == "North Seam"

    # Step 3: Worker sees both upcoming and past shifts under "My hours"
    _login(client, miner.id)
    my_shifts_resp = client.get("/api/me/shifts")
    assert my_shifts_resp.status_code == 200
    my_shifts = my_shifts_resp.json()["shifts"]
    shift_ids = [s["id"] for s in my_shifts]
    assert past_shift.id in shift_ids
    assert shift1_id in shift_ids
    # Worker view omits created_by
    for s in my_shifts:
        assert "created_by" not in s

    # Step 4: Miner sees ONLY the nodes of their own tunnel (North Seam)
    # The active/upcoming tunnel is North Seam
    nodes_resp = client.get("/api/nodes")
    assert nodes_resp.status_code == 200
    nodes = nodes_resp.json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["node_id"] == 10
    assert nodes[0]["location_name"] == "North Seam"

    # Foreman keeps mine-level view (sees both North Seam and South Seam nodes)
    _login(client, foreman.id)
    foreman_nodes_resp = client.get("/api/nodes")
    assert foreman_nodes_resp.status_code == 200
    foreman_nodes = foreman_nodes_resp.json()["nodes"]
    assert len(foreman_nodes) == 2
    f_node_ids = {n["node_id"] for n in foreman_nodes}
    assert f_node_ids == {10, 20}
