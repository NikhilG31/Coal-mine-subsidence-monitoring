"""Step 6 comprehensive test suite for QR badges, gate check-in/out, headcount, and check flags.

Owns automated verification of badge issuance, state transitions, headcount math, and flag cycles.
Must NOT interact with external production databases or hardcode system clock time.
"""
import datetime
import hashlib
import threading
import time
from unittest.mock import patch
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import (
    CHECK_FLAG_OVERDUE_HOURS,
    CHECKIN_EARLY_MIN,
    CHECKOUT_GRACE_MIN,
    SCAN_RATE_PER_MIN,
)
from app.database import SessionLocal, get_db
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
from app.services.badge_service import issue_badge, revoke_badge
from app.services.checkin_service import CheckInError, record_manual, record_scan
from app.services.flag_clearing import clear_flag
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

    for u in (manager, so, foreman, miner):
        db.add(UserMine(user_id=u.id, mine_id=mine.id))

    p_foreman = WorkerProfile(user_id=foreman.id, company_id=comp.id, employee_no="EMP-F01")
    p_miner = WorkerProfile(user_id=miner.id, company_id=comp.id, employee_no="EMP-M01")
    db.add_all([p_foreman, p_miner])
    db.commit()

    return {
        "company": comp,
        "mine": mine,
        "tunnel1": tunnel1,
        "tunnel2": tunnel2,
        "manager": manager,
        "so": so,
        "foreman": foreman,
        "miner": miner,
    }


def test_badge_lifecycle_storage_and_security(client, db):
    """Verifies SHA-256 storage, single active badge invariant, Cache-Control header, and no leaks."""
    env = _setup_test_env(db)
    mgr = env["manager"]
    miner = env["miner"]
    so = env["so"]

    # 1. Non-manager gets 403 on issue
    _login(client, so.id)
    res = client.post(f"/api/workers/{miner.id}/badge")
    assert res.status_code == 403

    # 2. Manager issues badge
    _login(client, mgr.id)
    res = client.post(f"/api/workers/{miner.id}/badge")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") == "no-store"
    data = res.json()
    plaintext_token = data["token"]
    assert plaintext_token.startswith("cmb_")
    assert "<svg" in data["badge_svg"]
    assert data["worker"]["id"] == miner.id
    assert data["worker"]["employee_no"] == "EMP-M01"

    # 3. Plaintext token is NOT in database; SHA-256 hash is stored
    expected_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()
    db_token = db.query(QRToken).filter(QRToken.user_id == miner.id, QRToken.revoked_at.is_(None)).first()
    assert db_token is not None
    assert db_token.token_hash == expected_hash
    assert plaintext_token not in db_token.token_hash

    # 4. Audit log does NOT contain plaintext token or hash
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


def test_badge_revocation_scoping(client, db):
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
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = now_during
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_revoke = client.delete(f"/api/workers/{miner.id}/badge")
        assert res_revoke.status_code == 200
        assert res_revoke.json()["revoked"] == 1


def test_scan_logic_state_transitions_and_dedup(client, db):
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
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = scan_time
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_in = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
        assert res_in.status_code == 200
        data_in = res_in.json()
        assert data_in["duplicate"] is False
        assert data_in["direction"] == "in"
        assert data_in["worker"]["id"] == miner.id

    # 2. Scan IN again within 5s -> duplicate 200
    dup_time = scan_time + datetime.timedelta(seconds=2)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = dup_time
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_dup = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
        assert res_dup.status_code == 200
        assert res_dup.json()["duplicate"] is True

    # 3. Scan IN after 10s -> 409 already_checked_in
    after_10s = scan_time + datetime.timedelta(seconds=10)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = after_10s
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_err = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
        assert res_err.status_code == 409
        assert res_err.json()["code"] == "already_checked_in"

    # 4. Scan OUT -> 200
    out_time = scan_time + datetime.timedelta(hours=4)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = out_time
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_out = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
        assert res_out.status_code == 200
        assert res_out.json()["direction"] == "out"

    # 5. Scan OUT while outside -> 409 not_checked_in
    after_out = out_time + datetime.timedelta(seconds=10)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = after_out
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_out_err = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
        assert res_out_err.status_code == 409
        assert res_out_err.json()["code"] == "not_checked_in"

    # 6. Unknown badge returns identical 404
    res_unknown = client.post("/api/checkins", json={"token": "cmb_fakeunknownbadge", "direction": "in", "tunnel_id": t1.id})
    assert res_unknown.status_code == 404

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


def test_flag_cycle_and_manual_clearing(client, db):
    """Verifies flag evaluation past grace, idempotency, and headcount correction on manual clear."""
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

    checkin_time = shift_start + datetime.timedelta(minutes=5)
    record_scan(db, mgr, token, "in", tunnel_id=t1.id, now=checkin_time)

    eval_time_early = shift_end + datetime.timedelta(minutes=10)
    res_early = run_flag_cycle(db, now=eval_time_early)
    assert res_early["raised"] == 0

    eval_time_past_grace = shift_end + datetime.timedelta(minutes=20)
    res_flag = run_flag_cycle(db, now=eval_time_past_grace)
    assert res_flag["raised"] == 1

    res_idempotent = run_flag_cycle(db, now=eval_time_past_grace)
    assert res_idempotent["raised"] == 0

    flag = db.query(CheckFlag).filter(CheckFlag.assignment_id == asgn.id).first()
    assert flag is not None
    assert flag.cleared_at is None

    hc_inside = headcount_by_tunnel(db, [t1.id], now=eval_time_past_grace)
    assert hc_inside[t1.id]["inside"] == 1

    res_short = client.patch(f"/api/check-flags/{flag.id}", json={"note": "ab"})
    assert res_short.status_code == 422

    clear_time = eval_time_past_grace + datetime.timedelta(minutes=5)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = clear_time
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
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
    with patch.object(runner, "run_watchdog_once", side_effect=RuntimeError("Watchdog network crashed")):
        with patch.object(runner, "run_flag_once", return_value={"raised": 0}) as mock_flag:
            res = runner.run_once()
            assert res["watchdog"] is None
            assert res["flags"] == {"raised": 0}
            assert mock_flag.called


def test_alembic_migration_downgrade_and_upgrade():
    """Tests clean Alembic downgrade -1 followed by upgrade head."""
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_step6_done_when_end_to_end(client, db):
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
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now + datetime.timedelta(minutes=5)
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_in = client.post("/api/checkins", json={"token": token, "direction": "in", "tunnel_id": t1.id})
        assert res_in.status_code == 200
        hc = client.get("/api/headcount").json()
        assert hc[str(t1.id)]["inside"] == 1

    # 3. Foreman scans badge OUT -> headcount goes DOWN
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now + datetime.timedelta(hours=4)
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_out = client.post("/api/checkins", json={"token": token, "direction": "out", "tunnel_id": t1.id})
        assert res_out.status_code == 200
        hc2 = client.get("/api/headcount").json()
        assert hc2[str(t1.id)]["inside"] == 0

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
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = clear_time
        mock_dt.side_effect = lambda *args, **kw: datetime.datetime(*args, **kw)
        res_clear = client.patch(f"/api/check-flags/{flag_id}", json={"note": "Confirmed left at main portal"})
        assert res_clear.status_code == 200

        flags_after = client.get("/api/check-flags?status=open").json()["flags"]
        assert len(flags_after) == 0

        hc_final = client.get("/api/headcount").json()
        assert hc_final[str(t1.id)]["inside"] == 0
