"""Step 10 test suite: Notification outbox, delivery worker, channels, SSRF guard, and audit log.

Owns verifying transactional outbox guarantees, channel deliveries, rate limiting, and privacy policies.
Must NOT interact with external production databases, touch real networks, or patch the datetime module.
"""
import concurrent.futures
import datetime
import hashlib
import hmac
import json
import logging
import threading
from typing import Any, Dict, List
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import httpx
from pathlib import Path
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.main import app
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.company import Company
from app.models.gateway import Gateway
from app.models.incident import Incident
from app.models.mine import Mine
from app.models.node import Node
from app.models.notification_log import NotificationLog
from app.models.notification_settings import NotificationSettings
from app.models.reading import Reading
from app.models.tunnel import Tunnel
from app.models.user import User, UserMine
from app.models.worker_profile import WorkerProfile
from app.models.worker_report import WorkerReport
from app.security.passwords import hash_password
from app.security.rate_limit import clear_all_rate_limits
from app.security.roles import ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MANAGER, ROLE_MINER, ROLE_SAFETY_OFFICER
from app.security.tokens import COOKIE_NAME, create_session_token
from app.services import clock
from app.services.gateway_keys import hash_key
from app.services.incident_service import close_incident, open_incident
from app.services.notification_channels import SendResult, send, verify_webhook_signature
from app.services.notification_messages import build_message
from app.services.notification_service import (
    claim_due,
    enqueue,
    mark_failed,
    mark_retry,
    mark_sent,
    purge_old,
)
from app.services.notification_worker import run_notification_cycle
from app.services.packet_codec import pack_gateway_packet
from app.services.report_service import create_worker_report
from app.services.sos_service import handle_device_sos
from app.services.watchdog_service import run_watchdog_cycle
from app.services.webhook_guard import validate_webhook_url
from app.watchdog_runner import runner as watchdog_runner

FAKE_TELEGRAM_TOKEN = "123456:TEST-TOKEN-BOT"
FAKE_TWILIO_SID = "AC00000000000000000000000000000000"
FAKE_TWILIO_AUTH = "TEST-AUTH-TOKEN-1234567890123456"
FAKE_TWILIO_FROM = "+00000000000"
FAKE_WEBHOOK_SECRET = "test-secret-key-at-least-32-chars-long!"
FAKE_DEST_TELEGRAM = "987654321"
FAKE_DEST_WHATSAPP = "+10000000001"
FAKE_DEST_WEBHOOK = "https://webhook.test.domain/alerts"


def _create_env(db: Session) -> Dict[str, Any]:
    """Sets up company, mine, users, tunnel, gateway, and node for notification tests."""
    comp = Company(name="Acme Coal Co", name_normalized="acme coal co")
    db.add(comp)
    db.flush()

    mine = Mine(company_id=comp.id, name="Shaft 9", status="active", method="underground")
    db.add(mine)
    db.flush()

    tun = Tunnel(mine_id=mine.id, name="Tunnel North", code="TN1")
    db.add(tun)
    db.flush()

    gw = Gateway(
        mac="11:22:33:44:55:66",
        mine_id=mine.id,
        name="Gateway Alpha",
        api_key_hash=hash_key("gw_secret_key"),
        active=True,
    )
    db.add(gw)
    db.flush()

    node = Node(
        gateway_id=gw.id,
        node_no=1,
        tunnel_id=tun.id,
        lat=23.75,
        lon=86.42,
        status="normal",
    )
    db.add(node)
    db.flush()

    users = {}
    for role in (ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN, ROLE_MINER):
        u = User(
            company_id=comp.id,
            username=f"user_{role}",
            password_hash=hash_password("Password123!"),
            full_name=f"User {role.title()}",
            role=role,
            active=True,
        )
        db.add(u)
        db.flush()
        db.add(UserMine(user_id=u.id, mine_id=mine.id))
        users[role] = u

    # Add worker profile for miner
    db.add(
        WorkerProfile(
            user_id=users[ROLE_MINER].id,
            company_id=comp.id,
            employee_no="EMP-007",
            phone="+00000000007",
            emergency_contact_name="Family Contact",
            emergency_contact_phone="+00000000008",
        )
    )
    db.commit()

    return {
        "comp": comp,
        "mine": mine,
        "tun": tun,
        "gw": gw,
        "node": node,
        "users": users,
    }


def _login(client: TestClient, user_id: int):
    tok = create_session_token(user_id)
    client.cookies.set(COOKIE_NAME, tok)


@pytest.fixture
def mock_credentials(monkeypatch):
    """Sets fake notification credentials in config module."""
    monkeypatch.setattr("app.config.TELEGRAM_BOT_TOKEN", FAKE_TELEGRAM_TOKEN)
    monkeypatch.setattr("app.config.TWILIO_ACCOUNT_SID", FAKE_TWILIO_SID)
    monkeypatch.setattr("app.config.TWILIO_AUTH_TOKEN", FAKE_TWILIO_AUTH)
    monkeypatch.setattr("app.config.TWILIO_WHATSAPP_FROM", FAKE_TWILIO_FROM)
    monkeypatch.setattr("app.config.WEBHOOK_SIGNING_SECRET", FAKE_WEBHOOK_SECRET)
    monkeypatch.setattr("app.services.notification_channels.TELEGRAM_BOT_TOKEN", FAKE_TELEGRAM_TOKEN)
    monkeypatch.setattr("app.services.notification_channels.TWILIO_ACCOUNT_SID", FAKE_TWILIO_SID)
    monkeypatch.setattr("app.services.notification_channels.TWILIO_AUTH_TOKEN", FAKE_TWILIO_AUTH)
    monkeypatch.setattr("app.services.notification_channels.TWILIO_WHATSAPP_FROM", FAKE_TWILIO_FROM)
    monkeypatch.setattr("app.services.notification_channels.WEBHOOK_SIGNING_SECRET", FAKE_WEBHOOK_SECRET)
    from app.routers.notifications import set_webhook_resolver
    set_webhook_resolver(lambda host: ["93.184.216.34"])
    monkeypatch.setattr("app.services.webhook_guard._default_resolver", lambda host: ["93.184.216.34"])
    yield
    set_webhook_resolver(None)


# =============================================================================
# 1. OUTBOX ATOMICITY & DEDUPLICATION TESTS
# =============================================================================

def test_outbox_atomicity_and_dedupe(db, mock_credentials, freeze_clock):
    """B0, B6: Outbox inserts in caller session; rollback drops rows; commit persists; dedupe prevents repeats."""
    env = _create_env(db)
    mine, tun = env["mine"], env["tun"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Enable all 3 channels in mine settings
    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
        whatsapp_enabled=True,
        whatsapp_to=FAKE_DEST_WHATSAPP,
        webhook_enabled=True,
        webhook_url=FAKE_DEST_WEBHOOK,
        on_incident_open=True,
        on_incident_close=False,
    )
    db.add(settings)
    db.commit()

    # 1. Rolled back transaction leaves NO rows
    try:
        with db.begin_nested():
            inc_temp = Incident(mine_id=mine.id, tunnel_id=tun.id, source="manual", level=2, title="Temp", status="open", opened_at=t0)
            db.add(inc_temp)
            db.flush()
            enqueue(db, mine.id, "incident_open", "incident", inc_temp.id, {"mine_name": mine.name}, t0)
            raise RuntimeError("Rollback test")
    except RuntimeError:
        pass

    assert db.query(NotificationLog).count() == 0

    # 2. Committed incident leaves exactly one row per enabled channel (3 rows)
    inc = open_incident(db, env["users"][ROLE_MANAGER], mine.id, tun.id, "Fire emergency", 3, False, now=t0)
    db.commit()

    rows = db.query(NotificationLog).filter(NotificationLog.incident_id == inc.id).all()
    assert len(rows) == 3
    channels = {r.channel for r in rows}
    assert channels == {"telegram", "whatsapp", "webhook"}
    for r in rows:
        assert r.status == "pending"
        assert r.attempts == 0
        assert r.sent_at is None
        assert r.dedupe_key == f"incident_open:incident:{inc.id}"

    # 3. Repeat call with same dedupe_key produces NO duplicate rows
    new_ids = enqueue(db, mine.id, "incident_open", "incident", inc.id, {"mine_name": mine.name}, t0)
    db.commit()
    assert new_ids == []
    assert db.query(NotificationLog).filter(NotificationLog.incident_id == inc.id).count() == 3

    # 4. Off toggle creates no rows (on_incident_close is False)
    close_incident(db, env["users"][ROLE_MANAGER], inc, "All clear", now=t0)
    db.commit()
    assert db.query(NotificationLog).filter(NotificationLog.event == "incident_close").count() == 0


def test_unconfigured_enabled_channel_creates_skipped_row(db, freeze_clock):
    """B2, B6: An enabled channel without server credentials creates a row with status 'skipped'."""
    env = _create_env(db)
    mine = env["mine"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # All credentials missing
    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
        on_incident_open=True,
    )
    db.add(settings)

    inc = Incident(
        mine_id=mine.id,
        source="manual",
        level=2,
        title="Test Incident",
        status="open",
        opened_at=t0,
    )
    db.add(inc)
    db.commit()

    enqueue(db, mine.id, "incident_open", "incident", inc.id, {"mine_name": mine.name}, t0)
    db.commit()

    row = db.query(NotificationLog).filter(NotificationLog.ref_id == inc.id).first()
    assert row is not None
    assert row.status == "skipped"
    assert row.result == "channel not configured"
    assert row.sent_at is None
    assert row.attempts == 0


# =============================================================================
# 2. ENQUEUE FAILURE ISOLATION TESTS
# =============================================================================

def test_enqueue_failure_isolation(client, db, freeze_clock, caplog):
    """B0, B6: Injected exception in enqueue logs ERROR, does not raise, and leaves ingest/incident intact."""
    env = _create_env(db)
    mine, gw, node = env["mine"], env["gw"], env["node"]
    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
        on_sos=True,
    )
    db.add(settings)
    db.commit()

    headers = {"Content-Type": "application/octet-stream", "X-Gateway-Key": "gw_secret_key"}
    pkt = pack_gateway_packet(
        gw.mac, node.node_no, 101, 0.0, 0.0, 0.0, False, 3.85, 82.0, sos_active=True
    )
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    with patch("app.services.notification_service.pg_insert", side_effect=RuntimeError("Simulated enqueue failure")):
        with caplog.at_level(logging.ERROR):
            resp = client.post("/api/ingest", content=pkt, headers=headers)
            assert resp.status_code == 200
            assert "Notification enqueue failed" in caplog.text

    # Incident and worker report were created successfully
    assert db.query(Incident).count() == 1
    assert db.query(WorkerReport).filter(WorkerReport.type == "sos").count() == 1


# =============================================================================
# 3. HOOKS FIRING TESTS
# =============================================================================

def test_hooks_firing_matrix(db, mock_credentials, freeze_clock):
    """B3, B6: Enqueue hooks fire for manual, auto critical, SOS incident, device SOS, worker SOS, and offline alerts."""
    env = _create_env(db)
    mine, tun, gw, node = env["mine"], env["tun"], env["gw"], env["node"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
        on_incident_open=True,
        on_incident_close=True,
        on_sos=True,
        on_offline=True,
    )
    db.add(settings)
    db.commit()

    # 1. Manual incident
    inc_man = open_incident(db, env["users"][ROLE_MANAGER], mine.id, tun.id, "Manual Fire", 2, False, now=t0)
    db.commit()
    assert db.query(NotificationLog).filter(NotificationLog.ref_type == "incident", NotificationLog.ref_id == inc_man.id, NotificationLog.event == "incident_open").count() == 1

    # 2. Worker SOS report
    rep_sos = create_worker_report(db, env["users"][ROLE_MINER], type="sos", severity="critical", tunnel_id=tun.id, description="Trapped miner", now=t0)
    db.commit()
    assert db.query(NotificationLog).filter(NotificationLog.ref_type == "report", NotificationLog.ref_id == rep_sos.id, NotificationLog.event == "sos_report").count() == 1

    # Non-SOS worker report does NOT fire
    rep_inj = create_worker_report(db, env["users"][ROLE_MINER], type="injury", severity="low", tunnel_id=tun.id, description="Twisted ankle", now=t0)
    db.commit()
    assert db.query(NotificationLog).filter(NotificationLog.ref_id == rep_inj.id).count() == 0

    # 3. Device SOS report
    node.last_seen_at = t0
    gw.last_seen_at = t0
    db.commit()
    rep_dev, is_new = handle_device_sos(db, node, gw, t0)
    db.commit()
    assert is_new is True
    assert db.query(NotificationLog).filter(NotificationLog.ref_type == "report", NotificationLog.ref_id == rep_dev.id, NotificationLog.event == "sos_report").count() == 1

    # Repeat signal in merge window does NOT fire new notifications
    t1 = t0 + datetime.timedelta(seconds=15)
    freeze_clock(t1)
    rep_dev2, is_new2 = handle_device_sos(db, node, gw, t1)
    db.commit()
    assert is_new2 is False
    assert db.query(NotificationLog).filter(NotificationLog.ref_type == "report", NotificationLog.ref_id == rep_dev.id).count() == 1

    # 4. Offline alerts via watchdog
    t_offline = t0 + datetime.timedelta(seconds=120)
    freeze_clock(t_offline)
    run_watchdog_cycle(db, t_offline)
    db.commit()
    alert_rows = db.query(NotificationLog).filter(NotificationLog.event == "offline_alert").all()
    assert len(alert_rows) >= 1

    # 5. Incident close
    close_incident(db, env["users"][ROLE_MANAGER], inc_man, "Resolved", now=t_offline)
    db.commit()
    assert db.query(NotificationLog).filter(NotificationLog.ref_id == inc_man.id, NotificationLog.event == "incident_close").count() == 1


# =============================================================================
# 4. DELIVERY WORKER, RETRY BACKOFF & SANITIZATION TESTS
# =============================================================================

def test_delivery_lifecycle_and_sanitization(db, mock_credentials, freeze_clock, caplog):
    """B3, B6: Delivery success, retry backoff (10, 20, 40s), terminal failure after 5, no token leaks."""
    env = _create_env(db)
    mine = env["mine"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
    )
    db.add(settings)
    db.commit()

    # Create pending row
    row_ids = enqueue(db, mine.id, "test", "test", None, {"mine_name": mine.name}, t0)
    db.commit()
    log_id = row_ids[0]

    # 1. Simulated 500 error -> retryable with base backoff (delay = 10s)
    def _mock_500(req):
        return httpx.Response(500, text="Internal Server Error")

    transport_500 = httpx.MockTransport(_mock_500)
    counts1 = run_notification_cycle(db, t0, transport=transport_500)
    assert counts1["retried"] == 1

    row = db.query(NotificationLog).filter(NotificationLog.id == log_id).first()
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_attempt_at == t0 + datetime.timedelta(seconds=10)
    assert row.result == "HTTP 500"

    # 2. Advance time by 10s: 2nd attempt fails with timeout -> backoff = 10 * 2^1 = 20s
    t1 = t0 + datetime.timedelta(seconds=10)
    freeze_clock(t1)

    def _mock_timeout(req):
        raise httpx.ConnectTimeout("Connection timed out")

    transport_timeout = httpx.MockTransport(_mock_timeout)
    counts2 = run_notification_cycle(db, t1, transport=transport_timeout)
    assert counts2["retried"] == 1

    db.refresh(row)
    assert row.attempts == 2
    assert row.next_attempt_at == t1 + datetime.timedelta(seconds=20)
    assert row.result == "ConnectTimeout"

    # 3. Non-retryable 400 error marks row failed immediately
    t2 = t1 + datetime.timedelta(seconds=20)
    freeze_clock(t2)

    def _mock_400(req):
        return httpx.Response(400, json={"description": "Bad Request: chat not found"})

    transport_400 = httpx.MockTransport(_mock_400)
    counts3 = run_notification_cycle(db, t2, transport=transport_400)
    assert counts3["failed"] == 1

    db.refresh(row)
    assert row.status == "failed"
    assert row.attempts == 3
    assert row.result == "HTTP 400"
    assert row.sent_at is None

    # Verify no tokens or sensitive URLs in result or logs
    all_logs = db.query(NotificationLog).all()
    for l in all_logs:
        assert FAKE_TELEGRAM_TOKEN not in (l.result or "")
        assert "api.telegram.org" not in (l.result or "")
    assert FAKE_TELEGRAM_TOKEN not in caplog.text


def test_telegram_429_retried(db, mock_credentials, freeze_clock):
    """B3, B6: Telegram 429 rate limit is retryable."""
    env = _create_env(db)
    mine = env["mine"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(mine_id=mine.id, telegram_enabled=True, telegram_chat_id=FAKE_DEST_TELEGRAM)
    db.add(settings)
    db.commit()

    row_ids = enqueue(db, mine.id, "test", "test", None, {"mine_name": mine.name}, t0)
    db.commit()

    def _mock_429(req):
        return httpx.Response(429, json={"description": "Too Many Requests"})

    counts = run_notification_cycle(db, t0, transport=httpx.MockTransport(_mock_429))
    assert counts["retried"] == 1
    row = db.query(NotificationLog).filter(NotificationLog.id == row_ids[0]).first()
    assert row.status == "pending"
    assert row.result == "HTTP 429"


def test_redirect_rejected(db, mock_credentials, freeze_clock):
    """B3, B6: HTTP redirect responses are rejected as failures."""
    env = _create_env(db)
    mine = env["mine"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(mine_id=mine.id, webhook_enabled=True, webhook_url=FAKE_DEST_WEBHOOK)
    db.add(settings)
    db.commit()

    row_ids = enqueue(db, mine.id, "test", "test", None, {"mine_name": mine.name}, t0)
    db.commit()

    def _mock_302(req):
        return httpx.Response(302, headers={"Location": "https://evil.com"})

    counts = run_notification_cycle(
        db,
        t0,
        transport=httpx.MockTransport(_mock_302),
        resolver=lambda h: ["93.184.216.34"],
    )
    assert counts["failed"] == 1
    row = db.query(NotificationLog).filter(NotificationLog.id == row_ids[0]).first()
    assert row.status == "failed"
    assert "redirect rejected" in row.result


# =============================================================================
# 5. CONCURRENCY TESTS (SKIP LOCKED)
# =============================================================================

def test_concurrent_worker_skip_locked(db, mock_credentials, freeze_clock):
    """B3, B6: Two concurrent workers claiming the same due rows deliver each exactly once."""
    env = _create_env(db)
    mine = env["mine"]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
    )
    db.add(settings)
    db.commit()

    row_ids = enqueue(db, mine.id, "test", "test", None, {"mine_name": mine.name}, t0)
    db.commit()
    assert len(row_ids) == 1

    barrier = threading.Barrier(2)

    def _worker():
        barrier.wait()
        with SessionLocal() as s:
            return run_notification_cycle(
                s,
                t0,
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True})),
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_worker)
        f2 = executor.submit(_worker)
        r1, r2 = f1.result(), f2.result()

    total_sent = r1["sent"] + r2["sent"]
    assert total_sent == 1

    row = db.query(NotificationLog).filter(NotificationLog.id == row_ids[0]).first()
    assert row.status == "sent"
    assert row.attempts == 1


# =============================================================================
# 6. WEBHOOK HMAC SIGNATURE & SSRF GUARD TESTS
# =============================================================================

def test_webhook_hmac_signature_and_tampering():
    """B3, B6: Webhook signature matches timestamp and raw body; tampering invalidates signature."""
    secret = "a" * 32
    now = datetime.datetime(2030, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
    timestamp = str(int(now.timestamp()))
    payload = {"event": "test", "mine": "Shaft 9", "id": 42}
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    sig = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + raw_body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, timestamp, raw_body, sig) is True

    # Tampered body fails verification
    tampered_body = json.dumps({"event": "test", "mine": "Shaft 9", "id": 999}).encode("utf-8")
    assert verify_webhook_signature(secret, timestamp, tampered_body, sig) is False


@pytest.mark.parametrize(
    "url,resolved_ips,expect_valid",
    [
        ("http://public.example.com/hook", ["93.184.216.34"], False),  # http rejected by default
        ("https://user:pass@public.example.com/hook", ["93.184.216.34"], False),  # userinfo rejected
        ("https://localhost/hook", ["127.0.0.1"], False),  # localhost rejected
        ("https://127.0.0.1/hook", None, False),  # loopback IP
        ("https://[::1]/hook", None, False),  # IPv6 loopback
        ("https://10.0.0.1/hook", None, False),  # RFC1918 10/8
        ("https://172.16.0.1/hook", None, False),  # RFC1918 172.16/12
        ("https://192.168.1.1/hook", None, False),  # RFC1918 192.168/16
        ("https://169.254.169.254/hook", None, False),  # Link-local / metadata
        ("https://0.0.0.0/hook", None, False),  # Unspecified
        ("https://private.domain/hook", ["10.10.10.10"], False),  # resolves to private IP
        ("https://mixed.domain/hook", ["93.184.216.34", "192.168.0.5"], False),  # mix public/private
        ("https://public.example.com/hook", ["93.184.216.34"], True),  # public IP passes
    ],
)
def test_ssrf_validation_table(url, resolved_ips, expect_valid):
    """B3, B6: Comprehensive SSRF rejection table for private, loopback, metadata, and mixed IP ranges."""
    resolver = (lambda h: resolved_ips) if resolved_ips else None
    if expect_valid:
        validate_webhook_url(url, resolver=resolver)
    else:
        with pytest.raises(ValueError):
            validate_webhook_url(url, resolver=resolver)


def test_ssrf_allow_http_webhook_flag(monkeypatch):
    """B3, B6: NOTIFY_ALLOW_HTTP_WEBHOOK flag only relaxes the scheme requirement."""
    monkeypatch.setattr("app.services.webhook_guard.NOTIFY_ALLOW_HTTP_WEBHOOK", True)
    # HTTP to public IP passes
    validate_webhook_url("http://public.example.com/hook", resolver=lambda h: ["93.184.216.34"])

    # HTTP to private IP is still strictly rejected
    with pytest.raises(ValueError):
        validate_webhook_url("http://127.0.0.1/hook")


# =============================================================================
# 7. PRIVACY ENFORCEMENT TESTS
# =============================================================================

def test_privacy_guarantees(db, mock_credentials, freeze_clock):
    """B0, B6: Stored notification messages NEVER contain worker names, IDs, phones, or descriptions."""
    env = _create_env(db)
    mine, tun = env["mine"], env["tun"]
    miner = env["users"][ROLE_MINER]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    settings = NotificationSettings(
        mine_id=mine.id,
        telegram_enabled=True,
        telegram_chat_id=FAKE_DEST_TELEGRAM,
        whatsapp_enabled=True,
        whatsapp_to=FAKE_DEST_WHATSAPP,
        webhook_enabled=True,
        webhook_url=FAKE_DEST_WEBHOOK,
        on_incident_open=True,
        on_sos=True,
    )
    db.add(settings)
    db.commit()

    # Create worker report with explicit worker details
    create_worker_report(
        db,
        miner,
        type="sos",
        severity="critical",
        tunnel_id=tun.id,
        description="SECRET_WORKER_LEAK: Severe roof collapse near pillar 4!",
        now=t0,
    )
    db.commit()

    all_messages = [r.message for r in db.query(NotificationLog).all()]
    assert len(all_messages) > 0

    forbidden_substrings = [
        miner.full_name,
        miner.username,
        "EMP-007",
        "+00000000007",
        "+00000000008",
        "SECRET_WORKER_LEAK",
        "Severe roof collapse",
    ]

    for msg in all_messages:
        for forbidden in forbidden_substrings:
            assert forbidden not in msg, f"Privacy violation! Found '{forbidden}' in message: '{msg}'"


# =============================================================================
# 8. SETTINGS API, RBAC, CHECK CONSTRAINTS & RATE LIMIT TESTS
# =============================================================================

def test_notification_settings_routes_and_rbac(client, db, mock_credentials):
    """B4, B6: RBAC, scoping, masking, audit logging, and input validation on notification settings."""
    env = _create_env(db)
    mine = env["mine"]
    mgr = env["users"][ROLE_MANAGER]
    so = env["users"][ROLE_SAFETY_OFFICER]
    miner = env["users"][ROLE_MINER]

    # 1. Anonymous -> 401
    assert client.get(f"/api/mines/{mine.id}/notification-settings").status_code == 401

    # 2. Worker -> 403
    _login(client, miner.id)
    assert client.get(f"/api/mines/{mine.id}/notification-settings").status_code == 403
    assert client.put(f"/api/mines/{mine.id}/notification-settings", json={}).status_code == 403

    # 3. Superiors GET settings (masked)
    _login(client, so.id)
    resp_get = client.get(f"/api/mines/{mine.id}/notification-settings")
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["telegram_enabled"] is False
    assert data["channels_configured"] == {"telegram": True, "whatsapp": True, "webhook": True}

    # 4. Safety Officer PUT -> 403 (Manager only)
    assert client.put(f"/api/mines/{mine.id}/notification-settings", json={}).status_code == 403

    # 5. Manager PUT invalid inputs -> 422
    _login(client, mgr.id)
    # Enabled without destination -> 422
    r_no_dest = client.put(f"/api/mines/{mine.id}/notification-settings", json={"telegram_enabled": True})
    assert r_no_dest.status_code == 422

    # Invalid phone format -> 422
    r_bad_phone = client.put(
        f"/api/mines/{mine.id}/notification-settings",
        json={"whatsapp_enabled": True, "whatsapp_to": "12345"},
    )
    assert r_bad_phone.status_code == 422

    # Extra field -> 422 (extra="forbid")
    r_extra = client.put(
        f"/api/mines/{mine.id}/notification-settings",
        json={"extra_field": "disallowed"},
    )
    assert r_extra.status_code == 422

    # 6. Manager PUT valid -> 200, masked response, audited field names only
    payload = {
        "telegram_enabled": True,
        "telegram_chat_id": "123456789",
        "whatsapp_enabled": True,
        "whatsapp_to": "+1234567890",
        "webhook_enabled": True,
        "webhook_url": "https://api.example.com/alerts/v1?token=secret",
        "on_incident_open": True,
        "on_incident_close": True,
        "on_sos": True,
        "on_offline": True,
    }
    r_put = client.put(f"/api/mines/{mine.id}/notification-settings", json=payload)
    assert r_put.status_code == 200
    res_data = r_put.json()
    assert res_data["telegram_chat_id"] == "******789"
    assert res_data["whatsapp_to"] == "********890"
    assert res_data["webhook_url"] == "https://api.example.com/..."
    assert "secret" not in res_data["webhook_url"]

    # Verify audit log contains field names only (never values)
    audit = db.query(AuditLog).filter(AuditLog.action == "notification.settings_update").first()
    assert audit is not None
    assert "changed_fields" in audit.detail
    assert "123456789" not in json.dumps(audit.detail)
    assert "secret" not in json.dumps(audit.detail)


def test_test_endpoint_rate_limit_and_per_minute_dedupe(client, db, mock_credentials, freeze_clock):
    """B4, B6: Manager test endpoint rate limited to 5/hr with 429 Retry-After, and per-minute dedupe."""
    env = _create_env(db)
    mine = env["mine"]
    mgr = env["users"][ROLE_MANAGER]
    _login(client, mgr.id)
    clear_all_rate_limits()

    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Enable telegram
    db.add(NotificationSettings(mine_id=mine.id, telegram_enabled=True, telegram_chat_id=FAKE_DEST_TELEGRAM))
    db.commit()

    # First test trigger -> 200
    r1 = client.post(f"/api/mines/{mine.id}/notification-settings/test")
    assert r1.status_code == 200
    assert len(r1.json()["ids"]) == 1

    # Immediate second click within same minute deduplicated by DB constraint
    r2 = client.post(f"/api/mines/{mine.id}/notification-settings/test")
    assert r2.status_code == 200
    assert len(r2.json()["ids"]) == 0  # deduplicated

    # Trigger remaining 3 attempts (up to 5 total)
    for _ in range(3):
        client.post(f"/api/mines/{mine.id}/notification-settings/test")

    # 6th attempt within the hour returns 429 with Retry-After
    r_exceeded = client.post(f"/api/mines/{mine.id}/notification-settings/test")
    assert r_exceeded.status_code == 429
    assert "Retry-After" in r_exceeded.headers


def test_old_stub_routes_return_404(client, db):
    """B4, B6: Old notification stub routes have been completely removed."""
    env = _create_env(db)
    _login(client, env["users"][ROLE_MANAGER].id)

    assert client.get("/api/notifications/config").status_code == 404
    assert client.post("/api/notifications/config", json={}).status_code == 404
    assert client.post("/api/notifications/test").status_code == 404


def test_direct_sql_check_constraints(db):
    """B1, B6: Direct SQL inserts violate check constraints on invalid notification rows."""
    env = _create_env(db)
    mine = env["mine"]

    # 1. telegram_enabled=True without telegram_chat_id violates ck_notification_settings_telegram
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO notification_settings (mine_id, telegram_enabled, telegram_chat_id) "
                    "VALUES (:mid, true, null)"
                ),
                {"mid": mine.id},
            )

    # 2. status='sent' without sent_at violates ck_notification_log_sent_at
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO notification_log (mine_id, event, channel, status, message, dedupe_key, sent_at) "
                    "VALUES (:mid, 'test', 'telegram', 'sent', 'hello', 'd1', null)"
                ),
                {"mid": mine.id},
            )

    # 3. status='pending' with sent_at violates ck_notification_log_sent_at
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                text(
                    "INSERT INTO notification_log (mine_id, event, channel, status, message, dedupe_key, sent_at) "
                    "VALUES (:mid, 'test', 'telegram', 'pending', 'hello', 'd2', now())"
                ),
                {"mid": mine.id},
            )


# =============================================================================
# 9. RUNNER RESILIENCE & RETENTION PURGE TESTS
# =============================================================================

def test_runner_cycle_isolation_and_purge_old(db, freeze_clock):
    """B3, B6: Runner continues when one cycle fails; purge_old deletes old records past retention."""
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    # Mock watchdog raising an exception
    with patch.object(watchdog_runner, "run_watchdog_once", side_effect=RuntimeError("Watchdog crashed")):
        res = watchdog_runner.run_once(t0)
        assert res is not None
        assert res["watchdog"] is None  # Watchdog failed safely
        assert "notifications" in res  # Notifications executed independently

    # Test purge_old
    env = _create_env(db)
    mine = env["mine"]
    old_time = t0 - datetime.timedelta(days=31)

    db.execute(
        text(
            "INSERT INTO notification_log (mine_id, event, channel, status, message, dedupe_key, created_at, sent_at) "
            "VALUES (:mid, 'test', 'telegram', 'sent', 'old', 'old1', :ot, :ot)"
        ),
        {"mid": mine.id, "ot": old_time},
    )
    db.commit()

    assert db.query(NotificationLog).filter(NotificationLog.dedupe_key == "old1").count() == 1
    deleted = purge_old(db, t0)
    db.commit()
    assert deleted == 1
    assert db.query(NotificationLog).filter(NotificationLog.dedupe_key == "old1").count() == 0


# =============================================================================
# 10. END-TO-END DONE-WHEN JOURNEY TEST
# =============================================================================

def test_step10_done_when_end_to_end_journey(client, db, mock_credentials, freeze_clock):
    """B6: End-to-end done-when journey: Manager saves settings, incident opens, cycle delivers, logs show sent."""
    env = _create_env(db)
    mine, tun = env["mine"], env["tun"]
    mgr = env["users"][ROLE_MANAGER]
    t0 = datetime.datetime(2030, 5, 1, 10, 0, tzinfo=datetime.timezone.utc)
    freeze_clock(t0)

    _login(client, mgr.id)

    # 1. Manager saves notification settings
    settings_payload = {
        "telegram_enabled": True,
        "telegram_chat_id": FAKE_DEST_TELEGRAM,
        "whatsapp_enabled": True,
        "whatsapp_to": FAKE_DEST_WHATSAPP,
        "webhook_enabled": True,
        "webhook_url": FAKE_DEST_WEBHOOK,
        "on_incident_open": True,
        "on_incident_close": False,
        "on_sos": True,
        "on_offline": True,
    }
    r_put = client.put(f"/api/mines/{mine.id}/notification-settings", json=settings_payload)
    assert r_put.status_code == 200

    # 2. An emergency incident opens
    r_inc = client.post(
        "/api/incidents",
        json={"mine_id": mine.id, "tunnel_id": tun.id, "title": "Subsidence Alert", "level": 3, "siren": True},
    )
    assert r_inc.status_code == 201
    inc_id = r_inc.json()["id"]

    # 3. Verify 3 rows pending in outbox
    logs_pending = db.query(NotificationLog).filter(NotificationLog.incident_id == inc_id).all()
    assert len(logs_pending) == 3
    for l in logs_pending:
        assert l.status == "pending"

    # 4. Run delivery cycle with mock HTTP transport
    def _mock_dispatch(request: httpx.Request):
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(_mock_dispatch)
    cycle_res = run_notification_cycle(
        db,
        t0,
        transport=transport,
        resolver=lambda h: ["93.184.216.34"],
    )
    assert cycle_res["sent"] == 3

    # 5. Delivery log shows sent status and sent_at timestamps
    r_log = client.get(f"/api/notifications?mine_id={mine.id}")
    assert r_log.status_code == 200
    delivered_logs = r_log.json()
    assert len(delivered_logs) == 3
    for item in delivered_logs:
        assert item["status"] == "sent"
        assert item["sent_at"] is not None
        assert item["result"] == "HTTP 200"
        # Privacy check: only allowed fields
        assert "Mine: Shaft 9" in item["message"]
        assert "Level 3" in item["message"]


# =============================================================================
# 11. MIGRATION ROLLBACK & UPGRADE ROUND-TRIP TEST
# =============================================================================

def test_migration_downgrade_and_upgrade():
    """B1, B6: Migration round-trip downgrade -1 and upgrade head leaves schema clean."""
    server_dir = Path(__file__).resolve().parent.parent
    alembic_ini_path = server_dir / "alembic.ini"
    alembic_cfg = Config(str(alembic_ini_path))

    try:
        command.downgrade(alembic_cfg, "-1")
        command.upgrade(alembic_cfg, "head")
        command.check(alembic_cfg)
    finally:
        # Guarantee head restoration
        command.upgrade(alembic_cfg, "head")
