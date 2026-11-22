"""Step 6 comprehensive test suite for QR badges, gate check-in/out, headcount, and check flags.

Owns automated verification of badge issuance, state transitions, headcount math, and flag cycles.
Must NOT interact with external production databases or patch the datetime module.
"""
import datetime
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.config import (
    CHECK_FLAG_OVERDUE_HOURS,
    CHECKIN_EARLY_MIN,
    CHECKOUT_GRACE_MIN,
    SCAN_RATE_PER_MIN,
)
from app.database import SessionLocal, engine, get_db
from app.models.assignment import Assignment
from app.models.check_event import CheckEvent
from app.models.check_flag import CheckFlag
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.mine import Mine
from app.models.qr_token import QRToken
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.security.passwords import hash_password
from app.security.rate_limit import clear_all_rate_limits
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_MINER,
    ROLE_SAFETY_OFFICER,
)
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services.badge_service import BadgeError, issue_badge, revoke_badge
from app.services.checkin_service import CheckInError, UnknownBadge, record_manual, record_scan
from app.services.flag_clearing import FlagClearError, clear_flag
from app.services.flag_service import run_flag_cycle
from app.services.headcount import get_latest_check_events, headcount_by_tunnel, inside_now
from app.watchdog_runner import WatchdogRunner


def _login(client, user_id: int):
    token = create_session_token(user_id)
    client.cookies.set(COOKIE_NAME, token)


def _setup_test_env(db: Session):
    comp = Company(name="Step6 Coal Corp", name_normalized="step6 coal corp")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Shaft 1 Mine", status="active", method="underground")
    db.add(mine)
    db.flush()

    tunnel1 = Tunnel(mine_id=mine.id, name="North Tunnel", code="T-N1")
    tunnel2 = Tunnel(mine_id=mine.id, name="South Tunnel", code="T-S1")
    db.add_all([tunnel1, tunnel2])
    db.flush()

    manager = User(
        company_id=comp.id,
        username="s6_mgr",
        full_name="Manager Six",
        role=ROLE_MANAGER,
        password_hash=hash_password("Pass@12345"),
        active=True,
    )
    so = User(
        company_id=comp.id,
        username="s6_so",
        full_name="Safety Officer Six",
        role=ROLE_SAFETY_OFFICER,
        password_hash=hash_password("Pass@12345"),
        active=True,
    )
    foreman = User(
        company_id=comp.id,
        username="s6_foreman",
        full_name="Foreman Six",
        role=ROLE_FOREMAN,
        password_hash=hash_password("Pass@12345"),
        active=True,
    )
    miner = User(
        company_id=comp.id,
        username="s6_miner",
        full_name="Miner Six",
        role=ROLE_MINER,
        password_hash=hash_password("Pass@12345"),
        active=True,
    )
    db.add_all([manager, so, foreman, miner])
    db.flush()

    # Link users to mine
    for u in (manager, so, foreman, miner):
        db.add(UserMine(user_id=u.id, mine_id=mine.id))

    # Profiles
    db.add(WorkerProfile(company_id=comp.id, user_id=miner.id, employee_no="EMP-001", phone="+919876543210"))
    db.add(WorkerProfile(company_id=comp.id, user_id=foreman.id, employee_no="EMP-002", phone="+919876543211"))
    db.commit()

    return {
        "company": comp,
        "mine": mine,
        "tunnel1": tunnel1,
        "tunnel2": tunnel2,
        "manager": manager,
        "safety_officer": so,
        "foreman": foreman,
        "miner": miner,
    }


def test_badge_lifecycle_storage_and_security(client, db):
    """Verifies token generation, SHA-256 storage, SVG rendering, and re-issuance revocation."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]

    _login(client, mgr.id)

    # 1. Issue badge
    res = client.post(f"/api/workers/{miner.id}/badge")
    assert res.status_code == 200
    data = res.json()
    assert "token" in data
    assert "badge_svg" in data
    assert data["badge_svg"].startswith("<svg")
    assert data["worker"]["id"] == miner.id

    plaintext_token = data["token"]
    assert plaintext_token.startswith("cmb_")
    assert res.headers.get("cache-control") == "no-store"

    # 2. Verify database stores only the SHA-256 hash
    db.expire_all()
    token_rows = db.query(QRToken).filter(QRToken.user_id == miner.id).all()
    assert len(token_rows) == 1
    stored_row = token_rows[0]
    expected_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
    assert stored_row.token_hash == expected_hash
    assert plaintext_token not in stored_row.token_hash

    # 3. Direct DB scan: plaintext token nowhere in qr_tokens
    raw_val = db.execute(text("SELECT token_hash FROM qr_tokens WHERE id = :id"), {"id": stored_row.id}).scalar()
    assert raw_val == expected_hash

    # 4. Audit log verification (detail must not contain token)
    audit_row = (
        db.execute(
            text("SELECT detail::text FROM audit_log WHERE action = 'badge.issue' AND entity_id = :uid"),
            {"uid": miner.id},
        )
        .mappings()
        .first()
    )
    assert audit_row is not None
    assert plaintext_token not in audit_row["detail"]
    assert expected_hash not in audit_row["detail"]

    # 5. Worker list and detail include has_badge=True
    w_detail = client.get(f"/api/workers/{miner.id}").json()
    assert w_detail["has_badge"] is True
    assert w_detail["badge_issued_at"] is not None
    assert plaintext_token not in str(w_detail)

    # 6. Reissuing revokes previous badge
    res2 = client.post(f"/api/workers/{miner.id}/badge")
    assert res2.status_code == 200
    new_token = res2.json()["token"]
    assert new_token != plaintext_token

    # Check database: exactly one active badge
    db.expire_all()
    active_tokens = db.query(QRToken).filter(QRToken.user_id == miner.id, QRToken.revoked_at.is_(None)).all()
    assert len(active_tokens) == 1
    assert active_tokens[0].token_hash == hashlib.sha256(new_token.encode("utf-8")).hexdigest()

    # Old token is revoked
    old_row = db.query(QRToken).filter(QRToken.token_hash == expected_hash).first()
    assert old_row.revoked_at is not None
    assert old_row.revoked_by == mgr.id


def test_badge_revocation_scoping(client, db, freeze_clock):
    """Verifies badge revocation permissions: manager anywhere, foreman only for current tunnel."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    # Issue badge to miner
    _login(client, mgr.id)
    res = client.post(f"/api/workers/{miner.id}/badge")
    assert res.status_code == 200

    # Foreman has no current tunnel -> gets 404
    _login(client, foreman.id)
    del_res = client.delete(f"/api/workers/{miner.id}/badge")
    assert del_res.status_code == 404

    # Assign foreman and miner to tunnel1
    fixed_start = datetime.datetime(2030, 1, 1, 8, 0, tzinfo=datetime.timezone.utc)
    fixed_end = datetime.datetime(2030, 1, 1, 16, 0, tzinfo=datetime.timezone.utc)
    now_during = datetime.datetime(2030, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)

    db.add(Assignment(user_id=foreman.id, tunnel_id=t1.id, starts_at=fixed_start, ends_at=fixed_end))
    db.add(Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=fixed_start, ends_at=fixed_end))
    db.commit()

    # Foreman revokes miner in same tunnel -> 200
    freeze_clock(now_during)
    res_revoke = client.delete(f"/api/workers/{miner.id}/badge")
    assert res_revoke.status_code == 200
    assert res_revoke.json()["revoked"] == 1


def test_scan_logic_state_transitions_and_dedup(client, db, freeze_clock):
    """Verifies in/out transitions, duplicate suppression within 5s, 409 conflicts, and rate limiting."""
    clear_all_rate_limits()
    env = _setup_test_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    s_start = datetime.datetime(2030, 2, 1, 8, 0, tzinfo=datetime.timezone.utc)
    s_end = datetime.datetime(2030, 2, 1, 16, 0, tzinfo=datetime.timezone.utc)
    db.add(Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s_start, ends_at=s_end))
    db.commit()

    scan_time = datetime.datetime(2030, 2, 1, 8, 5, tzinfo=datetime.timezone.utc)

    # 1. Scan IN -> 200
    freeze_clock(scan_time)
    res_in = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res_in.status_code == 200
    data_in = res_in.json()
    assert data_in["duplicate"] is False
    assert data_in["direction"] == "in"
    assert data_in["worker"]["id"] == miner.id

    # 2. Scan IN again within 5s -> duplicate 200
    dup_time = scan_time + datetime.timedelta(seconds=2)
    freeze_clock(dup_time)
    res_dup = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res_dup.status_code == 200
    assert res_dup.json()["duplicate"] is True

    # 3. Scan IN after 10s -> 409 already_checked_in
    after_10s = scan_time + datetime.timedelta(seconds=10)
    freeze_clock(after_10s)
    res_err = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res_err.status_code == 409
    assert res_err.json()["code"] == "already_checked_in"

    # 4. Scan OUT -> 200
    out_time = scan_time + datetime.timedelta(hours=4)
    freeze_clock(out_time)
    res_out = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
    assert res_out.status_code == 200
    assert res_out.json()["direction"] == "out"

    # 5. Scan OUT while outside -> 409 not_checked_in
    after_out = out_time + datetime.timedelta(seconds=10)
    freeze_clock(after_out)
    res_out_err = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
    assert res_out_err.status_code == 409
    assert res_out_err.json()["code"] == "not_checked_in"

    # 6. Unknown badge returns identical 404
    res_unknown = client.post("/api/checkins", json={"token": "cmb_fakeunknownbadge", "direction": "in", "tunnel_id": t1.id})
    assert res_unknown.status_code == 404
    assert res_unknown.json() == {"detail": "Badge not recognised"}

    # 7. Miner scanner -> 403
    _login(client, miner.id)
    res_miner_scan = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res_miner_scan.status_code == 403


def test_scan_rate_limiting(client, db):
    """Verifies 429 Retry-After on scan rate limits."""
    clear_all_rate_limits()
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    for _ in range(10):
        client.post("/api/checkins", json={"token": "cmb_nonexistent", "direction": "in", "tunnel_id": t1.id})

    res_limit = client.post("/api/checkins", json={"token": "cmb_nonexistent", "direction": "in", "tunnel_id": t1.id})
    assert res_limit.status_code == 429
    assert "Retry-After" in res_limit.headers
    clear_all_rate_limits()


def test_headcount_calculations(client, db):
    """Verifies live headcount via DISTINCT ON check events and active shift scheduled counts."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token_m = client.post(f"/api/workers/{miner.id}/badge").json()["token"]
    token_f = client.post(f"/api/workers/{foreman.id}/badge").json()["token"]

    now = datetime.datetime(2030, 3, 1, 9, 0, tzinfo=datetime.timezone.utc)
    db.add(Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=now - datetime.timedelta(hours=1), ends_at=now + datetime.timedelta(hours=7)))
    db.add(Assignment(user_id=foreman.id, tunnel_id=t1.id, starts_at=now - datetime.timedelta(hours=1), ends_at=now + datetime.timedelta(hours=7)))
    db.commit()

    hc1 = headcount_by_tunnel(db, [t1.id], now=now)
    assert hc1[t1.id]["inside"] == 0
    assert hc1[t1.id]["scheduled_now"] == 2

    record_scan(db, mgr, token_m, "in", tunnel_id=t1.id, now=now)
    hc2 = headcount_by_tunnel(db, [t1.id], now=now)
    assert hc2[t1.id]["inside"] == 1
    assert hc2[t1.id]["workers"][0]["id"] == miner.id

    record_scan(db, mgr, token_f, "in", tunnel_id=t1.id, now=now)
    hc3 = headcount_by_tunnel(db, [t1.id], now=now)
    assert hc3[t1.id]["inside"] == 2

    record_scan(db, mgr, token_m, "out", tunnel_id=t1.id, now=now + datetime.timedelta(hours=2))
    hc4 = headcount_by_tunnel(db, [t1.id], now=now + datetime.timedelta(hours=2))
    assert hc4[t1.id]["inside"] == 1
    assert hc4[t1.id]["workers"][0]["id"] == foreman.id


def test_flag_cycle_and_manual_clearing(client, db, freeze_clock):
    """Verifies that unclosed shifts generate check flags and clearance creates manual out events."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    shift_start = datetime.datetime(2030, 4, 1, 8, 0, tzinfo=datetime.timezone.utc)
    shift_end = datetime.datetime(2030, 4, 1, 16, 0, tzinfo=datetime.timezone.utc)
    asgn = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=shift_start, ends_at=shift_end)
    db.add(asgn)
    db.commit()

    # Check in within early window (07:50)
    checkin_time = shift_start - datetime.timedelta(minutes=10)
    record_scan(db, mgr, token, "in", tunnel_id=t1.id, now=checkin_time)

    # 1. Before grace expires (16:10) -> no flag
    eval_time_before = shift_end + datetime.timedelta(minutes=10)
    assert run_flag_cycle(db, now=eval_time_before)["raised"] == 0

    # 2. After grace expires (16:20) -> raises 1 flag
    eval_time_past_grace = shift_end + datetime.timedelta(minutes=20)
    assert run_flag_cycle(db, now=eval_time_past_grace)["raised"] == 1

    # Idempotent re-run
    assert run_flag_cycle(db, now=eval_time_past_grace)["raised"] == 0

    flags = db.query(CheckFlag).filter(CheckFlag.assignment_id == asgn.id).all()
    assert len(flags) == 1
    flag = flags[0]
    assert flag.cleared_at is None

    # 3. Clear flag via API with note
    res_short = client.patch(f"/api/check-flags/{flag.id}", json={"note": "ab"})
    assert res_short.status_code == 422

    clear_time = eval_time_past_grace + datetime.timedelta(minutes=5)
    freeze_clock(clear_time)
    res_clear = client.patch(
        f"/api/check-flags/{flag.id}",
        json={"note": "Worker confirmed left without badge scan"},
    )
    assert res_clear.status_code == 200
    assert res_clear.json()["cleared_at"] is not None

    hc_cleared = headcount_by_tunnel(db, [t1.id], now=clear_time)
    assert hc_cleared[t1.id]["inside"] == 0

    res_reclear = client.patch(f"/api/check-flags/{flag.id}", json={"note": "Second attempt"})
    assert res_reclear.status_code == 409


def test_concurrent_scan_barrier_race(db):
    """Verifies that two concurrent scan requests for the same worker result in exactly one success."""
    env = _setup_test_env(db)
    mgr_id = env["manager"].id
    miner_id = env["miner"].id
    t1_id = env["tunnel1"].id

    _, _, qr_tok = issue_badge(db, env["manager"], miner_id)
    token = f"cmb_mocktoken_{miner_id}"
    qr_tok.token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    db.commit()

    barrier = threading.Barrier(2)
    results = []

    def scan_worker():
        thread_db = SessionLocal()
        try:
            thread_mgr = thread_db.query(User).filter(User.id == mgr_id).first()
            barrier.wait()
            res = record_scan(thread_db, thread_mgr, token, "in", tunnel_id=t1_id)
            results.append(("ok", res))
        except CheckInError as exc:
            results.append(("err", exc))
        finally:
            thread_db.close()

    t1_thread = threading.Thread(target=scan_worker)
    t2_thread = threading.Thread(target=scan_worker)
    t1_thread.start()
    t2_thread.start()
    t1_thread.join()
    t2_thread.join()

    assert len(results) == 2
    successes = [r for r in results if r[0] == "ok" and not r[1]["duplicate"]]
    assert len(successes) == 1


def test_watchdog_runner_isolation(db):
    """Verifies that a failure in the watchdog cycle does not prevent the flag cycle from running."""
    runner = WatchdogRunner(grace_period_s=0)
    from unittest.mock import patch
    with patch.object(runner, "run_watchdog_once", side_effect=RuntimeError("Watchdog network crashed")):
        with patch.object(runner, "run_flag_once", return_value={"raised": 0}) as mock_flag:
            res = runner.run_once()
            assert res["watchdog"] is None
            assert res["flags"] == {"raised": 0}
            assert mock_flag.called


def test_alembic_migration_downgrade_and_upgrade():
    """Tests clean Alembic downgrade -1 followed by upgrade head."""
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    cfg = Config(str(alembic_ini_path))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    try:
        command.downgrade(cfg, "-1")
    finally:
        command.upgrade(cfg, "head")
        command.check(cfg)


def test_step6_done_when_end_to_end(client, db, freeze_clock):
    """Full end-to-end Done-When user journey for Step 6."""
    clear_all_rate_limits()
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    fixed_now = datetime.datetime(2030, 5, 1, 8, 0, tzinfo=datetime.timezone.utc)
    shift_end = fixed_now + datetime.timedelta(hours=8)
    db.add(Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=fixed_now, ends_at=shift_end))
    db.commit()

    # 1. Manager issues badge
    _login(client, mgr.id)
    res_issue = client.post(f"/api/workers/{miner.id}/badge")
    assert res_issue.status_code == 200
    token = res_issue.json()["token"]

    # 2. Foreman scans badge IN -> headcount goes UP
    freeze_clock(fixed_now + datetime.timedelta(minutes=5))
    res_in = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res_in.status_code == 200
    hc = client.get("/api/headcount").json()
    assert next(t for t in hc["tunnels"] if t["tunnel_id"] == t1.id)["inside"] == 1

    # 3. Foreman scans badge OUT -> headcount goes DOWN
    freeze_clock(fixed_now + datetime.timedelta(hours=4))
    res_out = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
    assert res_out.status_code == 200
    hc2 = client.get("/api/headcount").json()
    assert next(t for t in hc2["tunnels"] if t["tunnel_id"] == t1.id)["inside"] == 0

    # 4. Another shift ends without check-out
    shift2_start = datetime.datetime(2030, 5, 2, 8, 0, tzinfo=datetime.timezone.utc)
    shift2_end = datetime.datetime(2030, 5, 2, 16, 0, tzinfo=datetime.timezone.utc)
    asgn2 = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=shift2_start, ends_at=shift2_end)
    db.add(asgn2)
    db.commit()

    # Worker checks in at 08:02
    record_scan(db, mgr, token, "in", tunnel_id=t1.id, now=shift2_start + datetime.timedelta(minutes=2))

    # After shift end + grace (16:20), run flag cycle -> exactly ONE flag
    flag_res = run_flag_cycle(db, now=shift2_end + datetime.timedelta(minutes=20))
    assert flag_res["raised"] == 1

    # Verify flag appears in GET /api/check-flags
    flags_list = client.get("/api/check-flags?status=open").json()["flags"]
    assert len(flags_list) == 1
    flag_id = flags_list[0]["id"]

    # 5. Confirming "left, no scan" with note clears flag and fixes headcount
    clear_time = shift2_end + datetime.timedelta(minutes=25)
    freeze_clock(clear_time)
    res_clear = client.patch(f"/api/check-flags/{flag_id}", json={"note": "Confirmed left at main portal"})
    assert res_clear.status_code == 200

    flags_after = client.get("/api/check-flags?status=open").json()["flags"]
    assert len(flags_after) == 0

    hc_final = client.get("/api/headcount").json()
    assert next(t for t in hc_final["tunnels"] if t["tunnel_id"] == t1.id)["inside"] == 0


# =============================================================================
# PHASE A HARDENING & SPECIFICATION TESTS
# =============================================================================

def test_revoked_and_deactivated_badges_identical_404(client, db):
    """A9: Revoked-badge and deactivated-worker badge return identical 404 body to unknown badge."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]
    _login(client, mgr.id)

    # 1. Issue badge then revoke it
    tok_res = client.post(f"/api/workers/{miner.id}/badge").json()
    revoked_token = tok_res["token"]
    client.delete(f"/api/workers/{miner.id}/badge")

    # 2. Deactivate worker with active badge
    miner2 = User(company_id=env["company"].id, username="miner_deact", full_name="Deact Miner", role=ROLE_MINER, password_hash="hash", active=True)
    db.add(miner2)
    db.flush()
    db.add(UserMine(user_id=miner2.id, mine_id=env["mine"].id))
    db.commit()
    deact_tok_res = client.post(f"/api/workers/{miner2.id}/badge").json()
    deact_token = deact_tok_res["token"]
    miner2.active = False
    db.commit()

    # Compare responses with unknown badge
    res_unknown = client.post("/api/checkins", json={"token": "cmb_completelyunknown", "direction": "in", "tunnel_id": t1.id})
    res_revoked = client.post("/api/checkins", json={"token": revoked_token, "direction": "in", "tunnel_id": t1.id})
    res_deact = client.post("/api/checkins", json={"token": deact_token, "direction": "in", "tunnel_id": t1.id})

    assert res_unknown.status_code == 404
    assert res_revoked.status_code == 404
    assert res_deact.status_code == 404
    assert res_unknown.json() == res_revoked.json() == res_deact.json() == {"detail": "Badge not recognised"}


def test_scanner_mine_scoping_and_foreman_restrictions(client, db, freeze_clock):
    """A9: Scanner outside worker's mines -> 404; foreman scanning another tunnel -> 403."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner = env["miner"]
    t1 = env["tunnel1"]
    t2 = env["tunnel2"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    # Assign foreman to t1
    now = datetime.datetime(2030, 7, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)
    db.add(Assignment(user_id=foreman.id, tunnel_id=t1.id, starts_at=now - datetime.timedelta(hours=1), ends_at=now + datetime.timedelta(hours=7)))
    db.commit()

    # Foreman scanning t2 -> 403
    _login(client, foreman.id)
    res_f_t2 = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t2.id})
    assert res_f_t2.status_code == 403

    # Worker not belonging to tunnel's mine -> 404
    mine2 = Mine(company_id=env["company"].id, name="Shaft 2 Mine", status="active", method="underground")
    db.add(mine2)
    db.flush()
    db.add(UserMine(user_id=mgr.id, mine_id=mine2.id))
    t_m2 = Tunnel(mine_id=mine2.id, name="Shaft 2 Tunnel", code="T2-1")
    db.add(t_m2)
    db.commit()

    _login(client, mgr.id)
    res_wrong_mine = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t_m2.id})
    assert res_wrong_mine.status_code == 404
    assert res_wrong_mine.json()["detail"] == "Worker not found"


def test_no_active_shift_warning(client, db, freeze_clock):
    """A9: Scanning in without an active/imminent shift returns no_active_shift warning."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    now = datetime.datetime(2030, 8, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    res = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
    assert res.status_code == 200
    data = res.json()
    assert len(data["warnings"]) == 1
    assert data["warnings"][0]["code"] == "no_active_shift"


def test_early_checkin_grace_and_flag_boundaries(db):
    """A9: Early checkin > CHECKIN_EARLY_MIN, never checked in, and overdue flag boundary."""
    env = _setup_test_env(db)
    miner = env["miner"]
    t1 = env["tunnel1"]

    s_start = datetime.datetime(2030, 9, 1, 8, 0, tzinfo=datetime.timezone.utc)
    s_end = datetime.datetime(2030, 9, 1, 16, 0, tzinfo=datetime.timezone.utc)
    asgn = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s_start, ends_at=s_end)
    db.add(asgn)
    db.commit()

    # Checkin earlier than CHECKIN_EARLY_MIN (e.g. 150 min early > CHECKIN_EARLY_MIN=120)
    db.add(CheckEvent(user_id=miner.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=s_start - datetime.timedelta(minutes=150)))
    db.commit()

    # Run flag cycle past grace (16:20) -> no flag raised because check-in was too early
    now = s_end + datetime.timedelta(minutes=20)
    assert run_flag_cycle(db, now=now)["raised"] == 0

    # Overdue boundary: flag raised at T0 is overdue at exactly T0 + CHECK_FLAG_OVERDUE_HOURS
    t0 = datetime.datetime(2030, 9, 2, 10, 0, tzinfo=datetime.timezone.utc)
    flag = CheckFlag(user_id=miner.id, assignment_id=asgn.id, raised_at=t0)
    db.add(flag)
    db.commit()

    # Query with exactly CHECK_FLAG_OVERDUE_HOURS
    exact_overdue = t0 + datetime.timedelta(hours=CHECK_FLAG_OVERDUE_HOURS)
    overdue_cutoff = exact_overdue - datetime.timedelta(hours=CHECK_FLAG_OVERDUE_HOURS)
    assert flag.raised_at <= overdue_cutoff


def test_late_scan_out_autoclears_flag(client, db, freeze_clock):
    """A9: A late scan-out automatically clears open check flags."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]

    _login(client, mgr.id)
    token = client.post(f"/api/workers/{miner.id}/badge").json()["token"]

    s_start = datetime.datetime(2030, 10, 1, 8, 0, tzinfo=datetime.timezone.utc)
    s_end = datetime.datetime(2030, 10, 1, 16, 0, tzinfo=datetime.timezone.utc)
    asgn = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s_start, ends_at=s_end)
    db.add(asgn)
    db.commit()

    # In at 08:00
    record_scan(db, mgr, token, "in", tunnel_id=t1.id, now=s_start)

    # Flag raised at 16:30
    run_flag_cycle(db, now=s_end + datetime.timedelta(minutes=30))
    flag = db.query(CheckFlag).filter(CheckFlag.assignment_id == asgn.id).first()
    assert flag is not None
    assert flag.cleared_at is None

    # Late scan-out at 17:00
    late_out = s_end + datetime.timedelta(hours=1)
    freeze_clock(late_out)
    res_out = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
    assert res_out.status_code == 200

    db.refresh(flag)
    assert flag.cleared_at is not None
    assert flag.note == "scanned out late"


def test_flag_clearing_scoping_and_audit_privacy(client, db, freeze_clock, caplog):
    """A9: Foreman clears only own-tunnel flags; audit rows contain no note; no cmb_ in logs."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    foreman = env["foreman"]
    miner = env["miner"]
    t1 = env["tunnel1"]
    t2 = env["tunnel2"]

    s_start = datetime.datetime(2030, 11, 1, 8, 0, tzinfo=datetime.timezone.utc)
    s_end = datetime.datetime(2030, 11, 1, 16, 0, tzinfo=datetime.timezone.utc)
    asgn = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s_start, ends_at=s_end)
    db.add(asgn)
    db.flush()
    flag = CheckFlag(user_id=miner.id, assignment_id=asgn.id, raised_at=s_end + datetime.timedelta(minutes=20))
    db.add(flag)
    db.commit()

    # Foreman assigned to t2 trying to clear t1 flag -> 404
    now = s_end + datetime.timedelta(minutes=30)
    freeze_clock(now)
    db.add(Assignment(user_id=foreman.id, tunnel_id=t2.id, starts_at=s_start, ends_at=s_end + datetime.timedelta(hours=4)))
    db.commit()

    _login(client, foreman.id)
    res_f_clear = client.patch(f"/api/check-flags/{flag.id}", json={"note": "Left via gate"})
    assert res_f_clear.status_code == 404

    # Manager clears with sensitive note
    _login(client, mgr.id)
    with caplog.at_level(logging.INFO):
        res_m_clear = client.patch(f"/api/check-flags/{flag.id}", json={"note": "CONFIDENTIAL NOTE DO NOT LEAK"})
        assert res_m_clear.status_code == 200

    # Verify audit detail does not contain note
    audit_row = db.execute(text("SELECT detail::text FROM audit_log WHERE entity = 'check_flag' AND entity_id = :fid"), {"fid": flag.id}).mappings().first()
    assert audit_row is not None
    assert "CONFIDENTIAL" not in audit_row["detail"]
    assert "note" not in audit_row["detail"]

    # Verify no "cmb_" string in audit log or captured logs
    raw_audits = db.execute(text("SELECT detail::text FROM audit_log")).scalars().all()
    for row in raw_audits:
        assert "cmb_" not in row
    for record in caplog.records:
        assert "cmb_" not in record.message


def test_flag_clearing_three_cases(db, freeze_clock):
    """A1: Tests all three cases where flag is cleared without inserting a manual 'out' event."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    t1 = env["tunnel1"]
    t2 = env["tunnel2"]

    # Helper to create flagged assignment on given day
    def _create_flagged_shift(day: int):
        s_start = datetime.datetime(2030, 12, day, 8, 0, tzinfo=datetime.timezone.utc)
        s_end = datetime.datetime(2030, 12, day, 16, 0, tzinfo=datetime.timezone.utc)
        a = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s_start, ends_at=s_end)
        db.add(a)
        db.flush()
        f = CheckFlag(user_id=miner.id, assignment_id=a.id, raised_at=s_end + datetime.timedelta(minutes=20))
        db.add(f)
        db.commit()
        return a, f

    # Case 1: Worker already checked out -> clear flag, do NOT touch headcount (no out event)
    a1, f1 = _create_flagged_shift(1)
    now1 = a1.ends_at + datetime.timedelta(minutes=25)
    freeze_clock(now1)
    db.add(CheckEvent(user_id=miner.id, tunnel_id=t1.id, direction="out", method="qr", scanned_at=a1.ends_at - datetime.timedelta(minutes=5)))
    db.commit()

    ev_count_before = db.query(CheckEvent).count()
    clear_flag(db, mgr, f1, note="Worker already out", now=now1)
    assert f1.cleared_at is not None
    assert db.query(CheckEvent).count() == ev_count_before

    # Case 2: Worker is now inside a different tunnel -> clear flag, do NOT touch headcount
    a2, f2 = _create_flagged_shift(2)
    now2 = a2.ends_at + datetime.timedelta(minutes=25)
    freeze_clock(now2)
    db.add(CheckEvent(user_id=miner.id, tunnel_id=t2.id, direction="in", method="qr", scanned_at=a2.ends_at + datetime.timedelta(minutes=5)))
    db.commit()

    ev_count_before2 = db.query(CheckEvent).count()
    clear_flag(db, mgr, f2, note="Worker moved to south tunnel", now=now2)
    assert f2.cleared_at is not None
    assert db.query(CheckEvent).count() == ev_count_before2
    assert inside_now(db, [t2.id])[t2.id][0]["id"] == miner.id

    # Case 3: Worker is on a later shift -> clear flag, do NOT touch headcount
    a3, f3 = _create_flagged_shift(3)
    later_start = a3.ends_at + datetime.timedelta(hours=1)
    later_end = later_start + datetime.timedelta(hours=8)
    now3 = later_start + datetime.timedelta(minutes=10)
    freeze_clock(now3)
    db.add(Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=later_start, ends_at=later_end))
    db.add(CheckEvent(user_id=miner.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=later_start - datetime.timedelta(minutes=5)))
    db.commit()

    ev_count_before3 = db.query(CheckEvent).count()
    clear_flag(db, mgr, f3, note="Worker is active on next shift", now=now3)
    assert f3.cleared_at is not None
    assert db.query(CheckEvent).count() == ev_count_before3
    assert inside_now(db, [t1.id])[t1.id][0]["id"] == miner.id


def test_back_to_back_shifts_chaining(db):
    """A2: 08-16 then 16-24 with check-in at 07:55 and no out -> no flag at 16:15, 1 flag after 00:15."""
    env = _setup_test_env(db)
    miner = env["miner"]
    t1 = env["tunnel1"]

    s1_start = datetime.datetime(2031, 1, 1, 8, 0, tzinfo=datetime.timezone.utc)
    s1_end = datetime.datetime(2031, 1, 1, 16, 0, tzinfo=datetime.timezone.utc)
    s2_start = datetime.datetime(2031, 1, 1, 16, 0, tzinfo=datetime.timezone.utc)
    s2_end = datetime.datetime(2031, 1, 2, 0, 0, tzinfo=datetime.timezone.utc)

    asgn1 = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s1_start, ends_at=s1_end)
    asgn2 = Assignment(user_id=miner.id, tunnel_id=t1.id, starts_at=s2_start, ends_at=s2_end)
    db.add_all([asgn1, asgn2])
    db.flush()

    # One check-in at 07:55
    db.add(CheckEvent(user_id=miner.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=s1_start - datetime.timedelta(minutes=5)))
    db.commit()

    # At 16:15 (first shift ended + grace, but second shift is active and part of chain) -> NO flag
    t_1615 = s1_end + datetime.timedelta(minutes=15)
    res_1615 = run_flag_cycle(db, now=t_1615)
    assert res_1615["raised"] == 0

    # After 00:15 (e.g. 00:20) -> exactly ONE flag on second shift
    t_0020 = s2_end + datetime.timedelta(minutes=20)
    res_0020 = run_flag_cycle(db, now=t_0020)
    assert res_0020["raised"] == 1

    flag = db.query(CheckFlag).filter(CheckFlag.user_id == miner.id).first()
    assert flag is not None
    assert flag.assignment_id == asgn2.id

    # Normal out between shifts -> no flag
    miner2 = User(company_id=env["company"].id, username="miner_b2b_out", full_name="Miner Out", role=ROLE_MINER, password_hash="hash", active=True)
    db.add(miner2)
    db.flush()
    db.add(UserMine(user_id=miner2.id, mine_id=env["mine"].id))
    asgn3 = Assignment(user_id=miner2.id, tunnel_id=t1.id, starts_at=s1_start, ends_at=s1_end)
    asgn4 = Assignment(user_id=miner2.id, tunnel_id=t1.id, starts_at=s2_start, ends_at=s2_end)
    db.add_all([asgn3, asgn4])
    # Check in 07:55, check out 15:58
    db.add(CheckEvent(user_id=miner2.id, tunnel_id=t1.id, direction="in", method="qr", scanned_at=s1_start - datetime.timedelta(minutes=5)))
    db.add(CheckEvent(user_id=miner2.id, tunnel_id=t1.id, direction="out", method="qr", scanned_at=s1_end - datetime.timedelta(minutes=2)))
    db.commit()

    res_out_chain = run_flag_cycle(db, now=t_0020)
    assert res_out_chain["raised"] == 0


def test_concurrent_badge_issuance(db):
    """A6: Two concurrent issue calls for same worker produce 409 badge_busy or sequential active badge."""
    env = _setup_test_env(db)
    mgr_id = env["manager"].id
    miner_id = env["miner"].id

    barrier = threading.Barrier(2)
    results = []

    def issue_worker():
        thread_db = SessionLocal()
        try:
            thread_mgr = thread_db.query(User).filter(User.id == mgr_id).first()
            barrier.wait()
            res = issue_badge(thread_db, thread_mgr, miner_id)
            results.append(("ok", res))
        except BadgeError as exc:
            results.append(("err", exc))
        finally:
            thread_db.close()

    t1 = threading.Thread(target=issue_worker)
    t2 = threading.Thread(target=issue_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    # At least one succeeded, any failure must be 409 badge_busy
    errs = [r[1] for r in results if r[0] == "err"]
    for err in errs:
        assert err.status_code == 409
        assert err.code == "badge_busy"

    # In database, exactly one active badge exists
    active_tokens = db.query(QRToken).filter(QRToken.user_id == miner_id, QRToken.revoked_at.is_(None)).all()
    assert len(active_tokens) == 1

    # Revoke badge verifies company isolation
    other_comp = Company(name="Other Corp", name_normalized="other corp")
    db.add(other_comp)
    db.flush()
    foreign_mgr = User(company_id=other_comp.id, username="foreign_mgr", full_name="Foreign Mgr", role=ROLE_MANAGER, password_hash="hash", active=True)
    db.add(foreign_mgr)
    db.commit()

    with pytest.raises(BadgeError) as exc_info:
        revoke_badge(db, foreign_mgr, miner_id)
    assert exc_info.value.status_code == 404


def test_headcount_constant_query_count(db):
    """A3: Constant query count for headcount (10 vs 100 workers)."""
    comp = Company(name="Scale Test Corp", name_normalized="scale test corp")
    db.add(comp)
    db.flush()
    mine = Mine(company_id=comp.id, name="Scale Mine", status="active", method="underground")
    db.add(mine)
    db.flush()
    tunnel = Tunnel(mine_id=mine.id, name="Scale Tunnel", code="T-SCALE")
    db.add(tunnel)
    db.flush()

    now = datetime.datetime(2031, 2, 1, 10, 0, tzinfo=datetime.timezone.utc)

    def _add_workers(count: int, offset: int):
        for i in range(count):
            idx = offset + i
            u = User(company_id=comp.id, username=f"scale_u_{idx}", full_name=f"Scale Worker {idx}", role=ROLE_MINER, password_hash="hash", active=True)
            db.add(u)
            db.flush()
            db.add(UserMine(user_id=u.id, mine_id=mine.id))
            db.add(Assignment(user_id=u.id, tunnel_id=tunnel.id, starts_at=now - datetime.timedelta(hours=2), ends_at=now + datetime.timedelta(hours=6)))
            db.add(CheckEvent(user_id=u.id, tunnel_id=tunnel.id, direction="in", method="qr", scanned_at=now - datetime.timedelta(minutes=30)))
        db.commit()

    # 10 workers
    _add_workers(10, 0)
    db.expire_all()

    def count_queries():
        queries = 0
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            nonlocal queries
            queries += 1
        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        try:
            headcount_by_tunnel(db, [tunnel.id], now=now)
            return queries
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)

    q_10 = count_queries()

    # Add 90 more workers -> 100 workers total
    _add_workers(90, 10)
    db.expire_all()

    q_100 = count_queries()

    assert q_10 == q_100
    assert q_10 > 0


def test_foreman_headcount_scope_and_company_isolation(client, db, freeze_clock):
    """A3 & A9: Foreman gets only own tunnel in headcount; company isolation on headcount and flags."""
    env = _setup_test_env(db)
    foreman = env["foreman"]
    t1 = env["tunnel1"]
    t2 = env["tunnel2"]

    now = datetime.datetime(2031, 3, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(now)

    # Assign foreman to t1
    db.add(Assignment(user_id=foreman.id, tunnel_id=t1.id, starts_at=now - datetime.timedelta(hours=1), ends_at=now + datetime.timedelta(hours=7)))
    db.commit()

    _login(client, foreman.id)
    res_f = client.get("/api/headcount")
    assert res_f.status_code == 200
    tunnels = res_f.json()["tunnels"]
    assert len(tunnels) == 1
    assert tunnels[0]["tunnel_id"] == t1.id

    # Company isolation
    other_comp = Company(name="Foreign Company", name_normalized="foreign company")
    db.add(other_comp)
    db.flush()
    other_mine = Mine(company_id=other_comp.id, name="Other Mine", status="active", method="underground")
    db.add(other_mine)
    db.flush()
    other_mgr = User(company_id=other_comp.id, username="other_mgr", full_name="Other Manager", role=ROLE_MANAGER, password_hash="hash", active=True)
    db.add(other_mgr)
    db.commit()

    _login(client, other_mgr.id)
    # Requesting foreign mine headcount -> 404
    res_foreign_hc = client.get(f"/api/headcount?mine_id={env['mine'].id}")
    assert res_foreign_hc.status_code == 404

    # Requesting foreign flags -> 404
    res_foreign_flags = client.get(f"/api/check-flags?mine_id={env['mine'].id}")
    assert res_foreign_flags.status_code == 404


def test_check_flags_validation_and_pagination(client, db):
    """A7: Validate status pattern ^(open|all)$, limit (default 200, max 500), and offset."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    _login(client, mgr.id)

    # Invalid status pattern -> 422
    res_inv = client.get("/api/check-flags?status=closed")
    assert res_inv.status_code == 422

    res_inv2 = client.get("/api/check-flags?status=pending")
    assert res_inv2.status_code == 422

    # Valid status
    assert client.get("/api/check-flags?status=open").status_code == 200
    assert client.get("/api/check-flags?status=all").status_code == 200

    # Limit and offset
    assert client.get("/api/check-flags?limit=50&offset=10").status_code == 200
    assert client.get("/api/check-flags?limit=501").status_code == 422
