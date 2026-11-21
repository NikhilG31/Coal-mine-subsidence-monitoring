"""Notification and Alert Delivery System for Mine Subsidence Monitoring.

Features:
- Sensible deduplication: Alerts on level change (e.g. Normal -> Watch -> Warning -> Critical)
  and periodic repeat reminders (default 15 minutes) to avoid spamming every 5 seconds.
- In-app notification center stored in SQLite with acknowledgement tracking.
- Modular delivery integrations:
  1. In-App Alerts (built-in, default active)
  2. Webhook (Slack / Discord / Zapier / Custom automation)
  3. Telegram Bot API (Telegram channel / group / DM)
  4. Twilio WhatsApp & SMS (Direct cellular / WhatsApp messaging)
"""
import json
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple
import urllib.request
import urllib.parse


class BaseNotificationChannel:
    """Abstract base class for alert delivery channels."""
    name: str = "base"

    def is_configured(self, config: Dict[str, Any]) -> bool:
        return False

    def send(self, alert: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
        raise NotImplementedError


class InAppChannel(BaseNotificationChannel):
    name = "in_app"

    def is_configured(self, config: Dict[str, Any]) -> bool:
        return True

    def send(self, alert: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
        # Always succeeds since it's committed to database
        return True, "In-app alert recorded successfully"


class WebhookChannel(BaseNotificationChannel):
    name = "webhook"

    def is_configured(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("webhook_url", "").strip())

    def send(self, alert: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
        url = config.get("webhook_url", "").strip()
        if not url:
            return False, "Webhook URL not configured"
        try:
            payload = json.dumps({
                "event": "SUBSIDENCE_ALERT",
                "danger_level": alert["danger_level"].upper(),
                "node_id": alert["node_id"],
                "gateway_mac": alert["gateway_mac_address"],
                "trigger_reading": alert["trigger_reading"],
                "timestamp": alert["ts"],
                "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert["ts"])),
                "message": (
                    f"⚠️ MINE SUBSIDENCE ALERT: Node {alert['node_id']} (GW {alert['gateway_mac_address']}) "
                    f"reached {alert['danger_level'].upper()}! {alert['trigger_reading']}"
                )
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "MineSubsidenceMonitor/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, f"Webhook delivered (HTTP {resp.status})"
        except Exception as e:
            return False, f"Webhook failed: {e}"


class TelegramChannel(BaseNotificationChannel):
    name = "telegram"

    def is_configured(self, config: Dict[str, Any]) -> bool:
        return bool(config.get("telegram_bot_token") and config.get("telegram_chat_id"))

    def send(self, alert: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
        token = config.get("telegram_bot_token", "").strip()
        chat_id = config.get("telegram_chat_id", "").strip()
        if not token or not chat_id:
            return False, "Telegram token or chat_id missing"
        try:
            level_emoji = {
                "watch": "🟡",
                "warning": "🟠",
                "critical": "🚨",
                "normal": "🟢"
            }.get(alert["danger_level"].lower(), "⚠️")

            text = (
                f"{level_emoji} *MINE SUBSIDENCE ALERT: {alert['danger_level'].upper()}*\n\n"
                f"• *Node ID:* `{alert['node_id']}`\n"
                f"• *Gateway MAC:* `{alert['gateway_mac_address']}`\n"
                f"• *Trigger Reading:* {alert['trigger_reading']}\n"
                f"• *Time:* {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert['ts']))}\n"
                f"• *Status:* Immediate inspection recommended."
            )
            api_url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }).encode("utf-8")
            req = urllib.request.Request(api_url, data=data, headers={"User-Agent": "MineSubsidenceMonitor/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, "Telegram message sent"
        except Exception as e:
            return False, f"Telegram failed: {e}"


class TwilioChannel(BaseNotificationChannel):
    """Twilio integration for WhatsApp and SMS."""
    name = "twilio"

    def is_configured(self, config: Dict[str, Any]) -> bool:
        return bool(
            config.get("twilio_sid") and
            config.get("twilio_token") and
            config.get("twilio_from") and
            config.get("twilio_to")
        )

    def send(self, alert: Dict[str, Any], config: Dict[str, Any]) -> Tuple[bool, str]:
        sid = config.get("twilio_sid", "").strip()
        token = config.get("twilio_token", "").strip()
        from_num = config.get("twilio_from", "").strip()
        to_num = config.get("twilio_to", "").strip()
        is_whatsapp = config.get("twilio_is_whatsapp", False)

        if not (sid and token and from_num and to_num):
            return False, "Twilio SID, token, from, or to number missing"

        try:
            body = (
                f"ALERT: Node {alert['node_id']} subsidence status: {alert['danger_level'].upper()}. "
                f"Trigger: {alert['trigger_reading']}. GW: {alert['gateway_mac_address']}."
            )
            prefix = "whatsapp:" if is_whatsapp else ""
            api_url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
            data = urllib.parse.urlencode({
                "From": f"{prefix}{from_num}",
                "To": f"{prefix}{to_num}",
                "Body": body
            }).encode("utf-8")

            import base64
            auth_header = "Basic " + base64.b64encode(f"{sid}:{token}".encode("ascii")).decode("ascii")
            req = urllib.request.Request(api_url, data=data, headers={"Authorization": auth_header})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return True, f"Twilio {'WhatsApp' if is_whatsapp else 'SMS'} dispatched"
        except Exception as e:
            return False, f"Twilio failed: {e}"


CHANNELS = {
    "in_app": InAppChannel(),
    "webhook": WebhookChannel(),
    "telegram": TelegramChannel(),
    "twilio": TwilioChannel(),
}


def init_notification_tables(db: sqlite3.Connection):
    """Creates alert log and channel configuration tables."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        gateway_mac_address TEXT NOT NULL,
        node_id INTEGER NOT NULL,
        danger_level TEXT NOT NULL,
        prev_level TEXT,
        trigger_reading TEXT NOT NULL,
        details TEXT,
        acknowledged INTEGER NOT NULL DEFAULT 0,
        channel_results TEXT
    );

    CREATE TABLE IF NOT EXISTS notification_configs (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_alerts_gw_node ON alerts(gateway_mac_address, node_id, ts);
    CREATE INDEX IF NOT EXISTS idx_alerts_level ON alerts(danger_level, ts);
    """)

    # Seed default reminder interval if not present (15 minutes = 900 seconds)
    cur = db.execute("SELECT value FROM notification_configs WHERE key = 'repeat_interval_s'")
    if not cur.fetchone():
        db.execute(
            "INSERT INTO notification_configs (key, value) VALUES ('repeat_interval_s', '900')"
        )
        db.commit()


def get_notification_config(db: sqlite3.Connection) -> Dict[str, Any]:
    """Retrieves all channel configurations from SQLite."""
    rows = db.execute("SELECT key, value FROM notification_configs").fetchall()
    cfg = {r["key"]: r["value"] for r in rows}
    return {
        "repeat_interval_s": int(cfg.get("repeat_interval_s", 900)),
        "webhook_url": cfg.get("webhook_url", ""),
        "telegram_bot_token": cfg.get("telegram_bot_token", ""),
        "telegram_chat_id": cfg.get("telegram_chat_id", ""),
        "twilio_sid": cfg.get("twilio_sid", ""),
        "twilio_token": cfg.get("twilio_token", ""),
        "twilio_from": cfg.get("twilio_from", ""),
        "twilio_to": cfg.get("twilio_to", ""),
        "twilio_is_whatsapp": cfg.get("twilio_is_whatsapp", "0") in ("1", "true", "True"),
    }


def save_notification_config(db: sqlite3.Connection, cfg: Dict[str, Any]):
    """Saves notification configurations into SQLite."""
    for k, v in cfg.items():
        val = "1" if v is True else "0" if v is False else str(v or "")
        db.execute(
            "INSERT INTO notification_configs (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (k, val)
        )
    db.commit()


class AlertManager:
    """Handles alert generation with stateful deduplication and channel dispatch."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        # In-memory tracking of last known alert state:
        # key: (gateway_mac, node_id) -> {"level": str, "last_notified_ts": float}
        self.node_states: Dict[Tuple[str, int], Dict[str, Any]] = {}

    def process_reading(self, r: Dict[str, Any], analysis: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluates whether to generate an alert for this reading packet.
        
        Rules:
        1. Level changes (e.g. normal -> watch, watch -> warning, warning -> critical) ALWAYS trigger an alert.
        2. If status cleared (e.g. warning -> normal), generate a 'recovery' alert.
        3. If level remains high (watch/warning/critical) without change, suppress notification
           unless configured repeat_interval_s has elapsed.
        """
        mac = r["gateway_mac_address"]
        node_id = int(r["node_id"])
        current_level = analysis["status"].lower()
        now = time.time()
        key = (mac, node_id)

        cfg = get_notification_config(self.db)
        repeat_interval = cfg.get("repeat_interval_s", 900)

        prev_state = self.node_states.get(key)
        prev_level = prev_state["level"] if prev_state else "normal"
        last_ts = prev_state["last_notified_ts"] if prev_state else 0.0

        should_alert = False
        is_level_change = current_level != prev_level
        is_high_danger = current_level in ("watch", "warning", "critical")

        if is_level_change:
            # Always alert on any level change
            should_alert = True
        elif is_high_danger and (now - last_ts >= repeat_interval):
            # Repeat reminder for ongoing high alert
            should_alert = True

        # Update state cache
        self.node_states[key] = {
            "level": current_level,
            "last_notified_ts": now if should_alert else last_ts
        }

        if not should_alert:
            return None

        # Format triggering metric description
        tx, ty = r.get("tilt_x", 0.0), r.get("tilt_y", 0.0)
        vm = r.get("vibration_magnitude", 0.0)
        vd = r.get("vibration_detected", False)
        trigger_desc = (
            f"Tilt: ({tx:+.2f}°, {ty:+.2f}°) | "
            f"Vib RMS: {vm:.3f}g" + (" [Shock Detected]" if vd else "")
        )
        if analysis.get("reasons"):
            trigger_desc += " | " + "; ".join(analysis["reasons"][:2])

        # Dispatch alert through configured channels
        alert_record = {
            "ts": now,
            "gateway_mac_address": mac,
            "node_id": node_id,
            "danger_level": current_level,
            "prev_level": prev_level,
            "trigger_reading": trigger_desc,
            "details": json.dumps(analysis),
        }

        channel_results = {}
        for chan_name, chan in CHANNELS.items():
            if chan.is_configured(cfg):
                success, msg = chan.send(alert_record, cfg)
                channel_results[chan_name] = {"success": success, "message": msg}

        # Save to SQLite alerts table
        cur = self.db.execute(
            "INSERT INTO alerts (ts, gateway_mac_address, node_id, danger_level, prev_level, "
            "trigger_reading, details, acknowledged, channel_results) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (now, mac, node_id, current_level, prev_level, trigger_desc,
             json.dumps(analysis), json.dumps(channel_results))
        )
        self.db.commit()
        alert_record["id"] = cur.lastrowid
        alert_record["channel_results"] = channel_results
        return alert_record
