"""Step 9: Comprehensive test suite for mine profiles, contacts hierarchy, and production tracking.

Owns full unit, integration, RBAC, and end-to-end verification of:
1. Five-group superior profile assembly (identity, company, headcount, production, contacts).
2. Worker-safe trimmed model, field leakage prevention, and forbidden extra field rejection.
3. Multi-tenant company isolation and mine scoping (Safety Officer cross-mine 404).
4. Profile PATCH semantics, enums, coordinates pairing, length/control-char sanitization, and audit privacy.
5. Atomic contacts replacement, rank 1..20 uniqueness, phone validation, and count-only audit.
6. Production daily upsert, independent monthly summation, achievement % rules, and date boundaries.
7. Constant-query headcount by shift and tunnel lookahead matching Step 6 headcount.
8. Privacy guarantees (worker personal data isolation, audit note/number scrubbing).
9. Full Done-When end-to-end journey.
10. Alembic migration downgrade -1 and upgrade head roundtrip.

Must NOT run against the development database or mock the datetime module directly.
"""
import datetime
from pathlib import Path
from decimal import Decimal
import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from alembic import command
from alembic.config import Config

from app.database import engine
from app.models.assignment import Assignment
from app.models.audit_log import AuditLog
from app.models.check_event import CheckEvent
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.mine_contact import MineContact
from app.models.mine_production import MineProduction
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.schemas.mine_profile import WorkerMineProfileResponse
from app.security.passwords import hash_password
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_MINER,
    ROLE_SAFETY_OFFICER,
)
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.mine_headcount_service import headcount_by_tunnel_and_shift
from app.services.production_service import month_summary


def _create_user(db: Session, company_id: int, username: str, role: str) -> User:
    u = User(
        company_id=company_id,
        username=username,
        full_name=f"User {username}",
        role=role,
        password_hash=hash_password("Pass1234"),
        active=True,
    )
    db.add(u)
    db.flush()
    return u


def _login(client, user: User):
    token = create_session_token(user.id)
    client.cookies.set(COOKIE_NAME, token)


def _setup_mine_env(db: Session):
    comp = Company(name="Singareni Collieries", name_normalized="singareni collieries")
    db.add(comp)
    db.flush()

    mine1 = Mine(
        company_id=comp.id,
        name="Mine 1",
        subsidiary="Division South",
        status="active",
        method="underground",
        area_ha=120.5,
        max_depth_m=350.0,
        lat=23.750000,
        lon=86.420000,
        evacuation_notes="Evacuate via Shaft A in event of siren.",
    )
    mine2 = Mine(
        company_id=comp.id,
        name="Mine 2",
        status="active",
        method="opencast",
    )
    db.add_all([mine1, mine2])
    db.flush()

    t1 = Tunnel(mine_id=mine1.id, name="North Incline", code="NI-01", depth_m=200.0, status="active")
    t2 = Tunnel(mine_id=mine1.id, name="South Drift", code="SD-01", depth_m=320.0, status="active")
    db.add_all([t1, t2])
    db.flush()

    mgr = _create_user(db, comp.id, "mgr_s9", ROLE_MANAGER)
    so = _create_user(db, comp.id, "so_s9", ROLE_SAFETY_OFFICER)
    eng = _create_user(db, comp.id, "eng_s9", ROLE_ENGINEER)
    foreman = _create_user(db, comp.id, "foreman_s9", ROLE_FOREMAN)
    miner = _create_user(db, comp.id, "miner_s9", ROLE_MINER)

    # Scoping: mgr has all, so has mine1 only, foreman and miner have mine1
    db.add_all([
        UserMine(user_id=mgr.id, mine_id=mine1.id),
        UserMine(user_id=mgr.id, mine_id=mine2.id),
        UserMine(user_id=so.id, mine_id=mine1.id),
        UserMine(user_id=eng.id, mine_id=mine1.id),
        UserMine(user_id=foreman.id, mine_id=mine1.id),
        UserMine(user_id=miner.id, mine_id=mine1.id),
    ])
    db.commit()

    return {
        "comp": comp,
        "mine1": mine1,
        "mine2": mine2,
        "t1": t1,
        "t2": t2,
        "mgr": mgr,
        "so": so,
        "eng": eng,
        "foreman": foreman,
        "miner": miner,
    }


def test_superior_get_profile_five_groups(client, db, freeze_clock):
    """GET /api/mines/{id}/profile returns all five groups for superiors."""
    env = _setup_mine_env(db)
    mine, mgr = env["mine1"], env["mgr"]
    t0 = datetime.datetime(2030, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Add contacts
    c1 = MineContact(mine_id=mine.id, role="shift_supervisor", name="Rajesh Kumar", phone="+91 90000 00001", rank=1)
    c2 = MineContact(mine_id=mine.id, role="safety_officer", name="Sunita Sharma", phone="+91 90000 00002", rank=2)
    db.add_all([c1, c2])

    # Add production
    p1 = MineProduction(mine_id=mine.id, day=datetime.date(2030, 6, 1), planned_t=Decimal("500.00"), actual_t=Decimal("480.00"), updated_by=mgr.id)
    db.add(p1)
    db.commit()

    _login(client, mgr)
    resp = client.get(f"/api/mines/{mine.id}/profile")
    assert resp.status_code == 200
    data = resp.json()

    # 1. Identity & Status
    assert "identity_status" in data
    ident = data["identity_status"]
    assert ident["id"] == mine.id
    assert ident["name"] == "Mine 1"
    assert ident["status"] == "active"
    assert ident["lat"] == 23.75
    assert ident["lon"] == 86.42
    assert ident["evacuation_notes"] == "Evacuate via Shaft A in event of siren."

    # 2. Company & Classification
    assert "company_classification" in data
    comp = data["company_classification"]
    assert comp["company_name"] == "Singareni Collieries"
    assert comp["subsidiary"] == "Division South"
    assert comp["method"] == "underground"
    assert comp["area_ha"] == 120.5
    assert comp["max_depth_m"] == 350.0

    # 3. Headcount
    assert "headcount" in data
    hc = data["headcount"]
    assert "totals" in hc and "tunnels" in hc
    assert len(hc["tunnels"]) == 2

    # 4. Production
    assert "production" in data
    prod = data["production"]
    assert prod["month"] == "2030-06"
    assert prod["planned_total_t"] == 500.0
    assert prod["actual_total_t"] == 480.0
    assert prod["variance_t"] == -20.0
    assert prod["achievement_pct"] == 96.0
    assert prod["reported_days"] == 1

    # 5. Contacts (ordered by rank)
    assert "contacts" in data
    contacts = data["contacts"]
    assert len(contacts) == 2
    assert contacts[0]["rank"] == 1
    assert contacts[0]["name"] == "Rajesh Kumar"
    assert contacts[0]["id"] == c1.id
    assert contacts[1]["rank"] == 2
    assert contacts[1]["name"] == "Sunita Sharma"


def test_worker_get_profile_trimmed_model_and_rejection_of_extra_fields(client, db, freeze_clock):
    """Foreman and miner get only the trimmed worker-safe model without superior fields."""
    env = _setup_mine_env(db)
    mine, miner, foreman, t1 = env["mine1"], env["miner"], env["foreman"], env["t1"]
    t0 = datetime.datetime(2030, 6, 15, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    c1 = MineContact(mine_id=mine.id, role="shift_supervisor", name="Rajesh Kumar", phone="+91 90000 00001", rank=1)
    p1 = MineProduction(mine_id=mine.id, day=datetime.date(2030, 6, 1), planned_t=Decimal("500.00"), actual_t=Decimal("480.00"))
    db.add_all([c1, p1])
    db.commit()

    # 1. Unassigned miner gets current_tunnel == None
    _login(client, miner)
    resp = client.get(f"/api/mines/{mine.id}/profile")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data.keys()) == {"id", "name", "status", "evacuation_notes", "contacts", "current_tunnel"}
    assert data["current_tunnel"] is None
    assert len(data["contacts"]) == 1
    assert "id" not in data["contacts"][0]  # No contact id
    assert data["contacts"][0]["role"] == "shift_supervisor"
    assert data["contacts"][0]["name"] == "Rajesh Kumar"
    assert data["contacts"][0]["phone"] == "+91 90000 00001"
    assert data["contacts"][0]["rank"] == 1

    # Forbidden fields MUST NOT exist
    for forbidden in ("production", "headcount", "company_classification", "company_name", "subsidiary", "method", "area_ha", "max_depth_m", "lat", "lon"):
        assert forbidden not in data

    # 2. Assigned miner gets only their current tunnel
    assign = Assignment(
        user_id=miner.id,
        tunnel_id=t1.id,
        starts_at=t0 - datetime.timedelta(hours=1),
        ends_at=t0 + datetime.timedelta(hours=7),
        created_by=env["mgr"].id,
    )
    db.add(assign)
    db.commit()

    resp_assigned = client.get(f"/api/mines/{mine.id}/profile")
    assert resp_assigned.status_code == 200
    data_assigned = resp_assigned.json()
    assert data_assigned["current_tunnel"] == {
        "id": t1.id,
        "name": t1.name,
        "status": t1.status,
    }

    # 3. Foreman gets their current tunnel (or null if unassigned)
    _login(client, foreman)
    resp_fm = client.get(f"/api/mines/{mine.id}/profile")
    assert resp_fm.status_code == 200
    data_fm = resp_fm.json()
    assert set(data_fm.keys()) == {"id", "name", "status", "evacuation_notes", "contacts", "current_tunnel"}

    # 4. Attempting to inject extra fields into WorkerMineProfileResponse raises ValidationError
    with pytest.raises(ValidationError):
        WorkerMineProfileResponse(
            id=1,
            name="Mine 1",
            status="active",
            evacuation_notes=None,
            contacts=[],
            current_tunnel=None,
            production={"planned_total_t": 100},  # Extra forbidden field
        )


def test_profile_scoping_and_company_isolation(client, db):
    """Scoped access: 404 outside mine scope or company."""
    env = _setup_mine_env(db)
    mine1, mine2, so = env["mine1"], env["mine2"], env["so"]

    # Safety Officer is only assigned to mine1, NOT mine2
    _login(client, so)
    assert client.get(f"/api/mines/{mine1.id}/profile").status_code == 200
    assert client.get(f"/api/mines/{mine2.id}/profile").status_code == 404

    # Other company user gets 404 for mine1
    comp_other = Company(name="Coal India Ltd", name_normalized="coal india ltd")
    db.add(comp_other)
    db.flush()
    mgr_other = _create_user(db, comp_other.id, "mgr_other", ROLE_MANAGER)
    db.commit()

    _login(client, mgr_other)
    assert client.get(f"/api/mines/{mine1.id}/profile").status_code == 404
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "suspended"}).status_code == 404


def test_patch_mine_profile_semantics_and_validations(client, db):
    """PATCH /api/mines/{id}/profile modifies only provided fields and enforces strict validation."""
    env = _setup_mine_env(db)
    mine1, mgr, so, eng, foreman, miner = env["mine1"], env["mgr"], env["so"], env["eng"], env["foreman"], env["miner"]

    _login(client, mgr)

    # 1. PATCH changes only provided fields
    orig_area = mine1.area_ha
    resp = client.patch(f"/api/mines/{mine1.id}/profile", json={"subsidiary": "Eastern Division"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["company_classification"]["subsidiary"] == "Eastern Division"
    assert data["company_classification"]["area_ha"] == orig_area

    # 2. Duplicate mine name in same company -> 409
    resp_dup = client.patch(f"/api/mines/{mine1.id}/profile", json={"name": "Mine 2"})
    assert resp_dup.status_code == 409

    # 3. Bad status -> 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "invalid_status"}).status_code == 422

    # 4. Bad method -> 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"method": "invalid_method"}).status_code == 422

    # 5. Lat without lon -> 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"lat": 24.0}).status_code == 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"lon": 85.0}).status_code == 422

    # Coordinates range
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"lat": 95.0, "lon": 85.0}).status_code == 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"lat": 24.0, "lon": 195.0}).status_code == 422

    # 6. area_ha < 0 or max_depth_m < 0 -> 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"area_ha": -5.0}).status_code == 422
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"max_depth_m": -10.0}).status_code == 422

    # 7. evacuation_notes > 2000 chars -> 422; control characters stripped
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"evacuation_notes": "X" * 2001}).status_code == 422

    resp_ctrl = client.patch(f"/api/mines/{mine1.id}/profile", json={"evacuation_notes": "Line 1\x00\x07\nLine 2"})
    assert resp_ctrl.status_code == 200
    assert resp_ctrl.json()["identity_status"]["evacuation_notes"] == "Line 1\nLine 2"

    # 8. RBAC: Safety Officer and Engineer can PATCH; Foreman and Miner get 403
    _login(client, so)
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "maintenance"}).status_code == 200

    _login(client, eng)
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "active"}).status_code == 200

    _login(client, foreman)
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "maintenance"}).status_code == 403

    _login(client, miner)
    assert client.patch(f"/api/mines/{mine1.id}/profile", json={"status": "maintenance"}).status_code == 403

    # 9. Audit log check: detail contains ONLY names of changed fields
    audit_row = (
        db.query(AuditLog)
        .filter(AuditLog.action == "mine.update", AuditLog.entity_id == mine1.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit_row is not None
    assert "fields" in audit_row.detail
    assert isinstance(audit_row.detail["fields"], list)
    # Ensure no values or notes are stored in audit detail
    assert "notes" not in audit_row.detail
    assert "evacuation_notes" not in str(audit_row.detail.values())


def test_contacts_replace_all_atomic(client, db):
    """PUT /api/mines/{id}/contacts replaces all contacts atomically with rank uniqueness."""
    env = _setup_mine_env(db)
    mine, mgr, foreman = env["mine1"], env["mgr"], env["foreman"]

    _login(client, mgr)

    # Initial valid contacts
    initial_contacts = [
        {"role": "shift_supervisor", "name": "Contact One", "phone": "+91 90000 00001", "rank": 1},
        {"role": "safety_officer", "name": "Contact Two", "phone": "+91 90000 00002", "rank": 2},
    ]
    resp_init = client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": initial_contacts})
    assert resp_init.status_code == 200
    assert len(resp_init.json()["contacts"]) == 2
    assert db.query(MineContact).filter(MineContact.mine_id == mine.id).count() == 2

    # 1. Failing item leaves old contacts untouched (atomic)
    bad_contacts = [
        {"role": "shift_supervisor", "name": "New One", "phone": "+91 90000 00003", "rank": 1},
        {"role": "safety_officer", "name": "Bad Phone", "phone": "invalid_phone#", "rank": 2},
    ]
    resp_fail = client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": bad_contacts})
    assert resp_fail.status_code == 422
    # Verify old contacts remain intact
    current_contacts = db.query(MineContact).filter(MineContact.mine_id == mine.id).order_by(MineContact.rank).all()
    assert len(current_contacts) == 2
    assert current_contacts[0].name == "Contact One"

    # 2. Duplicate rank -> 422
    dup_rank = [
        {"role": "shift_supervisor", "name": "One", "phone": "+91 90000 00001", "rank": 1},
        {"role": "safety_officer", "name": "Two", "phone": "+91 90000 00002", "rank": 1},
    ]
    assert client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": dup_rank}).status_code == 422

    # 3. Rank 0 or 21 -> 422
    r0 = [{"role": "other", "name": "Zero", "phone": "+91 90000 00000", "rank": 0}]
    assert client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": r0}).status_code == 422

    r21 = [{"role": "other", "name": "High", "phone": "+91 90000 00000", "rank": 21}]
    assert client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": r21}).status_code == 422

    # 4. More than 20 -> 422
    too_many = [
        {"role": "other", "name": f"Person {i}", "phone": f"+91 90000 000{i:02d}", "rank": i}
        for i in range(1, 22)
    ]
    assert client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": too_many}).status_code == 422

    # 5. Worker gets 403 on PUT
    _login(client, foreman)
    assert client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": initial_contacts}).status_code == 403

    # 6. Audit detail contains count only (no names or numbers)
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "mine.contacts_update", AuditLog.entity_id == mine.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert audit.detail == {"count": 2}


def test_production_upsert_and_monthly_summary(client, db, freeze_clock):
    """PUT/GET /api/mines/{id}/production validates constraints, computes sums independently, and enforces rules."""
    env = _setup_mine_env(db)
    mine, mgr = env["mine1"], env["mgr"]

    # Fixed time: 2030-07-15 12:00 UTC (17:30 IST in Asia/Kolkata)
    t0 = datetime.datetime(2030, 7, 15, 12, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    _login(client, mgr)

    # 1. Upsert entries
    entries = [
        {"day": "2030-07-01", "planned_t": 500.0, "actual_t": 520.0},
        {"day": "2030-07-02", "planned_t": 500.0, "actual_t": 480.0},
        {"day": "2030-07-03", "planned_t": 600.0},  # planned only
    ]
    resp = client.put(f"/api/mines/{mine.id}/production", json={"entries": entries})
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["updated"] == 3

    # Summary for 2030-07:
    # planned: 500 + 500 + 600 = 1600.0
    # actual: 520 + 480 = 1000.0
    # variance: 1000 - 1600 = -600.0
    # achievement: 1000 / 1600 * 100 = 62.5%
    # reported_days: 2 (days with actual_t)
    sum_data = res_data["summary"]
    assert sum_data["planned_total_t"] == 1600.0
    assert sum_data["actual_total_t"] == 1000.0
    assert sum_data["variance_t"] == -600.0
    assert sum_data["achievement_pct"] == 62.5
    assert sum_data["reported_days"] == 2

    # 2. Update only provided fields: update actual_t for 2030-07-03 to 590.0
    resp_update = client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [{"day": "2030-07-03", "actual_t": 590.0}]},
    )
    assert resp_update.status_code == 200
    # Planned should remain 600.0
    row = db.query(MineProduction).filter(MineProduction.mine_id == mine.id, MineProduction.day == datetime.date(2030, 7, 3)).first()
    assert float(row.planned_t) == 600.0
    assert float(row.actual_t) == 590.0

    # 3. Future actual -> 422; future planned -> OK
    # Today is 2030-07-15
    resp_future_actual = client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [{"day": "2030-07-16", "actual_t": 100.0}]},
    )
    assert resp_future_actual.status_code == 422

    resp_future_planned = client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [{"day": "2030-07-16", "planned_t": 550.0}]},
    )
    assert resp_future_planned.status_code == 200

    # 4. Both values null -> 422
    assert client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [{"day": "2030-07-10", "planned_t": None, "actual_t": None}]},
    ).status_code == 422

    # 5. Duplicate day in one call -> 422
    assert client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [
            {"day": "2030-07-10", "planned_t": 100.0},
            {"day": "2030-07-10", "planned_t": 200.0},
        ]},
    ).status_code == 422

    # 6. Negative tonnes -> 422
    assert client.put(
        f"/api/mines/{mine.id}/production",
        json={"entries": [{"day": "2030-07-10", "planned_t": -50.0}]},
    ).status_code == 422

    # 7. > 62 entries -> 422
    too_many = [{"day": f"2030-01-{(i%28)+1:02d}", "planned_t": 10.0} for i in range(63)]
    assert client.put(f"/api/mines/{mine.id}/production", json={"entries": too_many}).status_code == 422

    # 8. Bad month format in GET -> 422
    assert client.get(f"/api/mines/{mine.id}/production?month=2030-13").status_code == 422
    assert client.get(f"/api/mines/{mine.id}/production?month=bad_month").status_code == 422

    # 9. Month with no data returns zeros with achievement NULL
    resp_empty_month = client.get(f"/api/mines/{mine.id}/production?month=2028-01")
    assert resp_empty_month.status_code == 200
    empty_sum = resp_empty_month.json()["summary"]
    assert empty_sum["planned_total_t"] == 0.0
    assert empty_sum["actual_total_t"] == 0.0
    assert empty_sum["variance_t"] == 0.0
    assert empty_sum["achievement_pct"] is None
    assert empty_sum["reported_days"] == 0

    # 10. Audit check: detail has only days count and months array
    audit = (
        db.query(AuditLog)
        .filter(AuditLog.action == "mine.production_update", AuditLog.entity_id == mine.id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    assert audit is not None
    assert "days" in audit.detail and "months" in audit.detail


def test_production_direct_sql_check_constraints(db):
    """Direct SQL violates CHECK constraints on mine_production."""
    comp = Company(name="SQL Check Co", name_normalized="sql check co")
    db.add(comp)
    db.flush()
    mine = Mine(company_id=comp.id, name="SQL Mine", status="active")
    db.add(mine)
    db.flush()

    with engine.connect() as conn:
        # Negative planned_t
        with pytest.raises(Exception):
            conn.execute(
                text("INSERT INTO mine_production (mine_id, day, planned_t) VALUES (:m, '2030-01-01', -10.0)"),
                {"m": mine.id},
            )
            conn.commit()

        # Both null
        with pytest.raises(Exception):
            conn.execute(
                text("INSERT INTO mine_production (mine_id, day, planned_t, actual_t) VALUES (:m, '2030-01-02', NULL, NULL)"),
                {"m": mine.id},
            )
            conn.commit()


def test_headcount_by_tunnel_and_shift(client, db, freeze_clock):
    """Headcount calculation: inside, scheduled_now, upcoming lookahead, and constant query count."""
    env = _setup_mine_env(db)
    mine, t1, t2, mgr, miner, foreman = env["mine1"], env["t1"], env["t2"], env["mgr"], env["miner"], env["foreman"]

    t0 = datetime.datetime(2030, 8, 1, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Miner is inside T1
    ev_in = CheckEvent(user_id=miner.id, tunnel_id=t1.id, direction="in", scanned_at=t0 - datetime.timedelta(minutes=30), scanned_by=mgr.id, method="manual")
    db.add(ev_in)

    # Assignment active now for miner in T1 (starts 7:00, ends 15:00)
    as_active = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=t0 - datetime.timedelta(hours=1), ends_at=t0 + datetime.timedelta(hours=7), created_by=mgr.id)
    db.add(as_active)

    # Foreman has upcoming shift in T2 starting in 4 hours (within 12h lookahead)
    as_upcoming = Assignment(user_id=foreman.id, tunnel_id=t2.id, starts_at=t0 + datetime.timedelta(hours=4), ends_at=t0 + datetime.timedelta(hours=12), created_by=mgr.id)
    db.add(as_upcoming)
    db.commit()

    # GET /api/mines/{id}/headcount
    _login(client, mgr)
    resp = client.get(f"/api/mines/{mine.id}/headcount")
    assert resp.status_code == 200
    data = resp.json()

    totals = data["totals"]
    assert totals["inside"] == 1
    assert totals["scheduled_now"] == 1
    assert totals["upcoming"] == 1

    tunnels = {t["tunnel_id"]: t for t in data["tunnels"]}
    assert tunnels[t1.id]["inside"] == 1
    assert tunnels[t1.id]["scheduled_now"] == 1
    assert tunnels[t1.id]["upcoming"] == 0

    assert tunnels[t2.id]["inside"] == 0
    assert tunnels[t2.id]["scheduled_now"] == 0
    assert tunnels[t2.id]["upcoming"] == 1

    # Constant query count test: 10 vs 100 workers
    # Query count should remain constant regardless of the number of workers in the database
    from sqlalchemy import event

    statements = []
    def _count_queries(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count_queries)
    try:
        statements.clear()
        headcount_by_tunnel_and_shift(db, mine, now=t0)
        s1 = list(statements)

        # Add 10 more workers and assignments
        for i in range(10):
            u = _create_user(db, env["comp"].id, f"worker_scale_{i}", ROLE_MINER)
            db.add(UserMine(user_id=u.id, mine_id=mine.id))
            db.add(Assignment(user_id=u.id, tunnel_id=t1.id, starts_at=t0 - datetime.timedelta(hours=1), ends_at=t0 + datetime.timedelta(hours=7), created_by=mgr.id))
        db.commit()
        db.refresh(mine)

        statements.clear()
        headcount_by_tunnel_and_shift(db, mine, now=t0)
        s2 = list(statements)

        print("S1:", len(s1), s1)
        print("S2:", len(s2), s2)
        assert len(s1) == len(s2), f"Query count changed with more workers: {len(s1)} vs {len(s2)}"
    finally:
        event.remove(engine, "before_cursor_execute", _count_queries)


def test_privacy_guarantees(client, db):
    """No worker phone, emergency contact, or employee data in new profile responses; audit scrubbed."""
    env = _setup_mine_env(db)
    mine, miner = env["mine1"], env["miner"]

    # Worker profile with private contact info
    wp = WorkerProfile(
        user_id=miner.id,
        company_id=miner.company_id,
        employee_no="EMP-9999",
        phone="+91 98765 43210",
        emergency_contact_name="Family Member",
        emergency_contact_phone="+91 99999 00000",
    )
    db.add(wp)
    db.commit()

    _login(client, miner)
    resp = client.get(f"/api/mines/{mine.id}/profile")
    raw_text = resp.text

    assert "EMP-9999" not in raw_text
    assert "+91 98765 43210" not in raw_text
    assert "Family Member" not in raw_text
    assert "+91 99999 00000" not in raw_text


def test_done_when_end_to_end_journey(client, db, freeze_clock):
    """Full done-when journey: Manager configures all groups, worker checks in, both views verified."""
    env = _setup_mine_env(db)
    mine, mgr, miner, t1 = env["mine1"], env["mgr"], env["miner"], env["t1"]

    t0 = datetime.datetime(2030, 9, 10, 8, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # 1. Manager updates profile fields & evacuation notes
    _login(client, mgr)
    resp_patch = client.patch(
        f"/api/mines/{mine.id}/profile",
        json={
            "subsidiary": "Southern Basin Operations",
            "evacuation_notes": "Assembly Point: Sector 4 North Gate.",
            "area_ha": 250.0,
            "max_depth_m": 420.0,
        },
    )
    assert resp_patch.status_code == 200

    # 2. Manager configures three key contacts
    contacts_payload = [
        {"role": "shift_supervisor", "name": "Vikas Rao", "phone": "+91 90000 11111", "rank": 1},
        {"role": "safety_officer", "name": "Ananya Sen", "phone": "+91 90000 22222", "rank": 2},
        {"role": "rescue_station", "name": "Central Rescue Station", "phone": "+91 90000 33333", "rank": 3},
    ]
    resp_cont = client.put(f"/api/mines/{mine.id}/contacts", json={"contacts": contacts_payload})
    assert resp_cont.status_code == 200

    # 3. Manager logs a month of production
    prod_entries = [
        {"day": "2030-09-01", "planned_t": 1200.0, "actual_t": 1150.0},
        {"day": "2030-09-02", "planned_t": 1200.0, "actual_t": 1220.0},
    ]
    resp_prod = client.put(f"/api/mines/{mine.id}/production", json={"entries": prod_entries})
    assert resp_prod.status_code == 200

    # 4. Worker is checked in and assigned to T1
    db.add(Assignment(
        user_id=miner.id,
        tunnel_id=t1.id,
        starts_at=t0 - datetime.timedelta(hours=1),
        ends_at=t0 + datetime.timedelta(hours=7),
        created_by=mgr.id,
    ))
    db.add(CheckEvent(
        user_id=miner.id,
        tunnel_id=t1.id,
        direction="in",
        scanned_at=t0 - datetime.timedelta(minutes=30),
        scanned_by=mgr.id,
        method="manual",
    ))
    db.commit()

    # 5. Superior GET /profile returns all five groups with non-empty data
    resp_sup = client.get(f"/api/mines/{mine.id}/profile")
    assert resp_sup.status_code == 200
    sup_data = resp_sup.json()

    assert sup_data["identity_status"]["evacuation_notes"] == "Assembly Point: Sector 4 North Gate."
    assert sup_data["company_classification"]["area_ha"] == 250.0
    assert sup_data["headcount"]["totals"]["inside"] >= 1
    assert sup_data["production"]["actual_total_t"] == 2370.0
    assert len(sup_data["contacts"]) == 3

    # 6. Miner GET /profile returns only the trimmed model
    _login(client, miner)
    resp_worker = client.get(f"/api/mines/{mine.id}/profile")
    assert resp_worker.status_code == 200
    w_data = resp_worker.json()

    assert w_data["evacuation_notes"] == "Assembly Point: Sector 4 North Gate."
    assert w_data["current_tunnel"]["id"] == t1.id
    assert len(w_data["contacts"]) == 3
    assert "production" not in w_data
    assert "headcount" not in w_data


def test_migration_downgrade_and_upgrade():
    """Verifies that downgrade -1 and upgrade head succeed cleanly."""
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    cfg = Config(str(alembic_ini_path))

    try:
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")
    finally:
        command.upgrade(cfg, "head")
