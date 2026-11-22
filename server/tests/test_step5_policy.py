"""Test suite for Step 5 policy enforcement and edge cases.

Owns verification of RBAC, employee profiles, shift redaction, scoping, and N+1 query prevention.
Must NOT execute tests against the production database or bypass tenant boundaries.
"""
import datetime
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.database import engine
from app.models.assignment import Assignment
from app.models.company import Company
from app.models.mine import Mine
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


def _create_user(db: Session, company_id: int, username: str, role: str) -> User:
    u = User(
        company_id=company_id,
        username=username,
        full_name=f"User {username}",
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


def test_engineer_can_create_shorten_delete_assignments(client, db):
    """A1 & A5: Engineer has full write permissions on /api/assignments (create, shorten, delete)."""
    comp = Company(name="Eng Co", name_normalized="eng co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Eng Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Eng Tunnel", code="ET-1", status="active")
    db.add(tunnel)
    db.flush()

    eng = _create_user(db, comp.id, "engineer_user", ROLE_ENGINEER)
    miner = _create_user(db, comp.id, "miner_user", ROLE_MINER)
    db.add_all([UserMine(user_id=eng.id, mine_id=mine.id), UserMine(user_id=miner.id, mine_id=mine.id)])
    db.commit()

    _login(client, eng.id)

    # 1. Create shift (future)
    s_start = "2029-05-10T08:00:00Z"
    s_end = "2029-05-10T16:00:00Z"
    r_create = client.post(
        "/api/assignments",
        json={"user_id": miner.id, "tunnel_id": tunnel.id, "starts_at": s_start, "ends_at": s_end},
    )
    assert r_create.status_code == 201, r_create.text
    assignment_id = r_create.json()["assignment"]["id"]

    # 2. Shorten shift
    new_end = "2029-05-10T14:00:00Z"
    r_patch = client.patch(f"/api/assignments/{assignment_id}", json={"ends_at": new_end})
    assert r_patch.status_code == 200, r_patch.text
    assert r_patch.json()["assignment"]["ends_at"].startswith("2029-05-10T14:00:00")

    # 3. Delete unstarted shift
    r_del = client.delete(f"/api/assignments/{assignment_id}")
    assert r_del.status_code == 200, r_del.text
    assert db.query(Assignment).filter(Assignment.id == assignment_id).first() is None


def test_foreman_and_miner_forbidden_on_workers_and_assignments(client, db):
    """A5: Foreman and Miner receive 403 on workers management and assignments routes."""
    comp = Company(name="Forbidden Co", name_normalized="forbidden co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Mine F", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Tunnel F", code="TF-1", status="active")
    db.add(tunnel)
    db.flush()

    foreman = _create_user(db, comp.id, "foreman_f", ROLE_FOREMAN)
    miner = _create_user(db, comp.id, "miner_f", ROLE_MINER)
    db.add_all([UserMine(user_id=foreman.id, mine_id=mine.id), UserMine(user_id=miner.id, mine_id=mine.id)])
    db.commit()

    for user in (foreman, miner):
        _login(client, user.id)
        assert client.get("/api/workers").status_code == 403
        assert client.get(f"/api/workers/{miner.id}").status_code == 403
        assert client.put(f"/api/workers/{miner.id}/profile", json={"employee_no": "E101"}).status_code == 403
        assert client.get("/api/assignments").status_code == 403
        assert client.post("/api/assignments", json={"user_id": miner.id, "tunnel_id": tunnel.id, "starts_at": "2029-01-01T08:00:00Z", "ends_at": "2029-01-01T16:00:00Z"}).status_code == 403
        assert client.patch("/api/assignments/1", json={"ends_at": "2029-01-01T14:00:00Z"}).status_code == 403
        assert client.delete("/api/assignments/1").status_code == 403


def test_safety_officer_mine_scoping_isolation(client, db):
    """A5: Safety Officer with [mineA] gets 404 for a worker assigned only to mineB."""
    comp = Company(name="Scope Comp", name_normalized="scope comp")
    db.add(comp)
    db.flush()

    mine_a = Mine(company_id=comp.id, name="Mine A", status="active", method="underground")
    mine_b = Mine(company_id=comp.id, name="Mine B", status="active", method="underground")
    db.add_all([mine_a, mine_b])
    db.flush()

    so = _create_user(db, comp.id, "so_scoper", ROLE_SAFETY_OFFICER)
    miner_b = _create_user(db, comp.id, "miner_only_b", ROLE_MINER)
    db.add(UserMine(user_id=so.id, mine_id=mine_a.id))
    db.add(UserMine(user_id=miner_b.id, mine_id=mine_b.id))
    db.commit()

    _login(client, so.id)

    # Detail route returns 404
    r_detail = client.get(f"/api/workers/{miner_b.id}")
    assert r_detail.status_code == 404
    assert r_detail.json()["detail"] == "Worker not found"

    # User_id filter on /api/assignments returns 404
    r_asgn = client.get(f"/api/assignments?user_id={miner_b.id}")
    assert r_asgn.status_code == 404
    assert r_asgn.json()["detail"] == "Worker not found"


def test_worker_profile_validations_duplicate_phone_manager(client, db):
    """A2 & A5: duplicate employee_no -> 409; bad phone -> 422; profile for a manager -> 400."""
    comp = Company(name="Profile Val Co", name_normalized="profile val co")
    db.add(comp)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_prof", ROLE_MANAGER)
    other_mgr = _create_user(db, comp.id, "mgr_other", ROLE_MANAGER)
    miner1 = _create_user(db, comp.id, "miner_p1", ROLE_MINER)
    miner2 = _create_user(db, comp.id, "miner_p2", ROLE_MINER)

    # Miner1 already has employee_no "EMP-001"
    wp1 = WorkerProfile(user_id=miner1.id, company_id=comp.id, employee_no="EMP-001")
    db.add(wp1)
    db.commit()

    _login(client, mgr.id)

    # 1. Profile for manager -> 400
    r_mgr = client.put(f"/api/workers/{other_mgr.id}/profile", json={"employee_no": "MGR-001"})
    assert r_mgr.status_code == 400
    assert "foreman or miner" in r_mgr.json()["detail"]

    # 2. Duplicate employee_no for miner2 -> 409
    r_dup = client.put(f"/api/workers/{miner2.id}/profile", json={"employee_no": "EMP-001"})
    assert r_dup.status_code == 409
    assert r_dup.json()["detail"] == "Employee number already in use"

    # 3. Bad phone -> 422
    r_phone = client.put(f"/api/workers/{miner2.id}/profile", json={"employee_no": "EMP-002", "phone": "invalid#phone"})
    assert r_phone.status_code == 422


def test_cross_company_assignment_patch_delete_404(client, db):
    """A5: Trying to PATCH or DELETE another company's assignment returns 404."""
    comp_a = Company(name="Comp Alpha", name_normalized="comp alpha")
    comp_b = Company(name="Comp Beta", name_normalized="comp beta")
    db.add_all([comp_a, comp_b])
    db.flush()

    mine_a = Mine(company_id=comp_a.id, name="Mine Alpha", status="active", method="underground")
    mine_b = Mine(company_id=comp_b.id, name="Mine Beta", status="active", method="underground")
    db.add_all([mine_a, mine_b])
    db.flush()

    t_b = Tunnel(mine_id=mine_b.id, name="Tunnel Beta", code="TB-1", status="active")
    db.add(t_b)
    db.flush()

    mgr_a = _create_user(db, comp_a.id, "mgr_alpha", ROLE_MANAGER)
    miner_b = _create_user(db, comp_b.id, "miner_beta", ROLE_MINER)
    db.add(UserMine(user_id=mgr_a.id, mine_id=mine_a.id))
    db.add(UserMine(user_id=miner_b.id, mine_id=mine_b.id))

    # Assignment in company B
    asgn_b = Assignment(
        user_id=miner_b.id,
        tunnel_id=t_b.id,
        starts_at=datetime.datetime(2029, 3, 1, 8, 0, tzinfo=datetime.timezone.utc),
        ends_at=datetime.datetime(2029, 3, 1, 16, 0, tzinfo=datetime.timezone.utc),
    )
    db.add(asgn_b)
    db.commit()

    _login(client, mgr_a.id)

    # Manager A tries to PATCH company B's assignment -> 404
    r_patch = client.patch(f"/api/assignments/{asgn_b.id}", json={"ends_at": "2029-03-01T14:00:00Z"})
    assert r_patch.status_code == 404

    # Manager A tries to DELETE company B's assignment -> 404
    r_del = client.delete(f"/api/assignments/{asgn_b.id}")
    assert r_del.status_code == 404


def test_redacted_clash_for_out_of_scope_mine(client, db):
    """A4 & A5: When clashing shift is in a mine outside caller's scope, clash info is redacted."""
    comp = Company(name="Multi Mine Co", name_normalized="multi mine co")
    db.add(comp)
    db.flush()

    mine_allowed = Mine(company_id=comp.id, name="Mine Allowed", status="active", method="underground")
    mine_secret = Mine(company_id=comp.id, name="Mine Secret", status="active", method="underground")
    db.add_all([mine_allowed, mine_secret])
    db.flush()

    t_allowed = Tunnel(mine_id=mine_allowed.id, name="Tunnel Allowed", code="TA-1", status="active")
    t_secret = Tunnel(mine_id=mine_secret.id, name="Tunnel Secret", code="TS-1", status="active")
    db.add_all([t_allowed, t_secret])
    db.flush()

    so = _create_user(db, comp.id, "so_redact", ROLE_SAFETY_OFFICER)
    miner = _create_user(db, comp.id, "miner_multi", ROLE_MINER)
    # SO only assigned to mine_allowed; miner assigned to both
    db.add(UserMine(user_id=so.id, mine_id=mine_allowed.id))
    db.add(UserMine(user_id=miner.id, mine_id=mine_allowed.id))
    db.add(UserMine(user_id=miner.id, mine_id=mine_secret.id))

    # Pre-existing shift in mine_secret
    clash_start = datetime.datetime(2029, 8, 1, 10, 0, tzinfo=datetime.timezone.utc)
    clash_end = datetime.datetime(2029, 8, 1, 18, 0, tzinfo=datetime.timezone.utc)
    existing_shift = Assignment(
        user_id=miner.id,
        tunnel_id=t_secret.id,
        starts_at=clash_start,
        ends_at=clash_end,
    )
    db.add(existing_shift)
    db.commit()

    _login(client, so.id)

    # SO tries to create overlapping shift in tunnel_allowed
    r = client.post(
        "/api/assignments",
        json={
            "user_id": miner.id,
            "tunnel_id": t_allowed.id,
            "starts_at": "2029-08-01T12:00:00Z",
            "ends_at": "2029-08-01T16:00:00Z",
        },
    )
    assert r.status_code == 409
    body = r.json()
    assert body["code"] == "shift_overlap"
    clash = body["clashing_shift"]
    # Redacted: starts_at and ends_at present; NO id, tunnel_id, or tunnel_name!
    assert "starts_at" in clash
    assert "ends_at" in clash
    assert "id" not in clash
    assert "tunnel_id" not in clash
    assert "tunnel_name" not in clash


def test_assignments_default_window_and_limit(client, db):
    """A3 & A5: GET /api/assignments uses now-7d..now+30d default window and respects limit."""
    comp = Company(name="Window Co", name_normalized="window co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Window Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="Window Tunnel", code="WT-1", status="active")
    db.add(tunnel)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_win", ROLE_MANAGER)
    miner = _create_user(db, comp.id, "miner_win", ROLE_MINER)
    db.add(UserMine(user_id=miner.id, mine_id=mine.id))
    db.flush()

    now = datetime.datetime.now(datetime.timezone.utc)
    # 1. Inside default window (now + 2 days)
    asgn_inside = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=now + datetime.timedelta(days=2),
        ends_at=now + datetime.timedelta(days=2, hours=8),
    )
    # 2. Outside default window (now + 40 days)
    asgn_outside = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=now + datetime.timedelta(days=40),
        ends_at=now + datetime.timedelta(days=40, hours=8),
    )
    # 3. Outside default window in the past (now - 15 days)
    asgn_past = Assignment(
        user_id=miner.id,
        tunnel_id=tunnel.id,
        starts_at=now - datetime.timedelta(days=15),
        ends_at=now - datetime.timedelta(days=15, hours=-8),
    )
    db.add_all([asgn_inside, asgn_outside, asgn_past])
    db.commit()

    _login(client, mgr.id)

    # Default query (no from/to)
    r = client.get("/api/assignments")
    assert r.status_code == 200
    ids = [a["id"] for a in r.json()["assignments"]]
    assert asgn_inside.id in ids
    assert asgn_outside.id not in ids
    assert asgn_past.id not in ids

    # Respects limit parameter
    r_lim = client.get("/api/assignments?limit=1")
    assert r_lim.status_code == 200
    assert len(r_lim.json()["assignments"]) <= 1


def test_me_profile_returns_caller_profile_only(client, db):
    """A5: /api/me/profile returns only caller's own profile and never another user's."""
    comp = Company(name="Me Profile Co", name_normalized="me profile co")
    db.add(comp)
    db.flush()

    m1 = _create_user(db, comp.id, "miner_me1", ROLE_MINER)
    m2 = _create_user(db, comp.id, "miner_me2", ROLE_MINER)
    wp1 = WorkerProfile(user_id=m1.id, company_id=comp.id, employee_no="M1-999")
    wp2 = WorkerProfile(user_id=m2.id, company_id=comp.id, employee_no="M2-888")
    db.add_all([wp1, wp2])
    db.commit()

    _login(client, m1.id)
    r1 = client.get("/api/me/profile")
    assert r1.status_code == 200
    assert r1.json()["employee_no"] == "M1-999"

    _login(client, m2.id)
    r2 = client.get("/api/me/profile")
    assert r2.status_code == 200
    assert r2.json()["employee_no"] == "M2-888"


def test_sql_query_count_for_50_shifts(client, db):
    """A3 & A5: Assert SQL query count for 50 shifts is constant and does not suffer N+1."""
    comp = Company(name="Query Count Co", name_normalized="query count co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="QC Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel = Tunnel(mine_id=mine.id, name="QC Tunnel", code="QCT-1", status="active")
    db.add(tunnel)
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_qc", ROLE_MANAGER)
    workers = []
    for i in range(10):
        w = _create_user(db, comp.id, f"worker_qc_{i}", ROLE_MINER)
        wp = WorkerProfile(user_id=w.id, company_id=comp.id, employee_no=f"EMP-QC-{i}")
        db.add(wp)
        workers.append(w)
    db.commit()

    # Create 50 non-overlapping shifts across workers (5 shifts per worker)
    base_time = datetime.datetime(2029, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)
    shifts = []
    for w_idx, w in enumerate(workers):
        for s_idx in range(5):
            st = base_time + datetime.timedelta(days=s_idx * 2)
            en = st + datetime.timedelta(hours=8)
            shifts.append(Assignment(user_id=w.id, tunnel_id=tunnel.id, starts_at=st, ends_at=en))
    db.add_all(shifts)
    db.commit()

    _login(client, mgr.id)

    query_count = 0

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal query_count
        query_count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        r = client.get(
            "/api/assignments?from=2029-09-01T00:00:00Z&to=2029-11-01T00:00:00Z&limit=100"
        )
        assert r.status_code == 200
        assert len(r.json()["assignments"]) == 50
        # Should be a constant number of queries (joinedload: 1 or 2 queries total), NOT 50+ queries
        assert query_count <= 4, f"Expected <= 4 queries due to eager loading, got {query_count}"
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
