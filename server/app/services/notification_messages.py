"""Message template formatting service for external notifications.

Owns formatting plain-text alert messages for Telegram, WhatsApp, and Webhooks under strict privacy rules.
Must NOT include worker names, employee numbers, phone numbers, personal notes, or report descriptions.
"""
import datetime
from typing import Dict, Optional, Union

from app.services import clock

__all__ = ["build_message"]


def build_message(
    event: str,
    mine_name: str,
    tunnel_name: Optional[str] = None,
    node_label: Optional[str] = None,
    level: Optional[Union[int, str]] = None,
    counts: Optional[Dict[str, int]] = None,
    now: Optional[datetime.datetime] = None,
    link: Optional[str] = None,
) -> str:
    """Constructs sanitized, privacy-enforced notification body (plain text <= 500 chars).

    Strict privacy policy:
    Built ONLY from event type, mine name, tunnel name (or node label / gateway name),
    incident level, counts (e.g. roster size), UTC time, and optional dashboard link.
    NEVER worker names, employee IDs, phones, report descriptions, or notes.
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    time_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")
    link_suffix = f"\nDashboard: {link}" if link else ""

    if event == "incident_open":
        loc = tunnel_name if tunnel_name else "Mine-wide"
        lvl_str = f"Level {level}" if level is not None else "Critical"
        roster_size = counts.get("roster_size", 0) if counts else 0
        msg = (
            f"[ALERT: INCIDENT OPEN] Mine: {mine_name} | Location: {loc} | "
            f"{lvl_str} | Checked-in: {roster_size} | Time: {time_str}{link_suffix}"
        )
    elif event == "incident_close":
        loc = tunnel_name if tunnel_name else "Mine-wide"
        msg = (
            f"[ALERT: INCIDENT CLOSED] Mine: {mine_name} | Location: {loc} | "
            f"Time: {time_str}{link_suffix}"
        )
    elif event == "sos_report":
        loc = tunnel_name or node_label or "Mine-wide"
        msg = (
            f"[EMERGENCY: SOS REPORT] Mine: {mine_name} | Location: {loc} | "
            f"Time: {time_str}{link_suffix}"
        )
    elif event == "offline_alert":
        dev = node_label or tunnel_name or "Device"
        msg = (
            f"[OFFLINE ALERT] Mine: {mine_name} | Device: {dev} offline | "
            f"Time: {time_str}{link_suffix}"
        )
    elif event == "test":
        msg = (
            f"[TEST NOTIFICATION] Mine: {mine_name} | "
            f"Test alert dispatched at {time_str}{link_suffix}"
        )
    else:
        msg = (
            f"[NOTIFICATION: {event.upper()}] Mine: {mine_name} | "
            f"Time: {time_str}{link_suffix}"
        )

    # Hard cap at 500 characters
    if len(msg) > 500:
        msg = msg[:497] + "..."
    return msg
