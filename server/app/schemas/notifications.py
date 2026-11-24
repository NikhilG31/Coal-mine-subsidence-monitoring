"""Pydantic schemas and serialization models for notification configurations and audit logs.

Owns validation constraints, input field forbidding, destination masking, and response models.
Must NOT query the database or enforce authorization checks directly.
"""
import datetime
import re
from typing import Dict, List, Optional
import urllib.parse
from pydantic import BaseModel, ConfigDict, Field, field_validator

TELEGRAM_CHAT_ID_REGEX = re.compile(r"^(-?[0-9]{1,20}|@[A-Za-z0-9_]{5,32})$")
WHATSAPP_TO_REGEX = re.compile(r"^\+[1-9][0-9]{6,14}$")


def mask_telegram_chat_id(val: Optional[str]) -> Optional[str]:
    """Masks all but the last 3 characters of a Telegram chat ID."""
    if not val:
        return None
    val = val.strip()
    if len(val) <= 3:
        return "***" + val
    return "*" * (len(val) - 3) + val[-3:]


def mask_whatsapp(val: Optional[str]) -> Optional[str]:
    """Masks all but the last 3 digits of a WhatsApp phone number."""
    if not val:
        return None
    val = val.strip()
    if len(val) <= 3:
        return "***" + val
    return "*" * (len(val) - 3) + val[-3:]


def mask_webhook_url(val: Optional[str]) -> Optional[str]:
    """Masks webhook URL leaving only scheme and netloc/host with path/query replaced by '...'."""
    if not val:
        return None
    val = val.strip()
    try:
        parsed = urllib.parse.urlparse(val)
        return f"{parsed.scheme}://{parsed.netloc}/..."
    except Exception:
        return "https://.../..."


class UpdateNotificationSettingsRequest(BaseModel):
    """Payload to update notification settings and destinations for a mine."""
    model_config = ConfigDict(extra="forbid")

    telegram_enabled: bool = False
    telegram_chat_id: Optional[str] = None
    whatsapp_enabled: bool = False
    whatsapp_to: Optional[str] = None
    webhook_enabled: bool = False
    webhook_url: Optional[str] = None

    on_incident_open: bool = True
    on_incident_close: bool = False
    on_sos: bool = True
    on_offline: bool = True


class ChannelsConfiguredView(BaseModel):
    """Server-level configuration availability per channel."""
    model_config = ConfigDict(extra="forbid")

    telegram: bool
    whatsapp: bool
    webhook: bool


class NotificationSettingsResponse(BaseModel):
    """Masked notification settings view for superiors and manager."""
    model_config = ConfigDict(extra="forbid")

    mine_id: int
    telegram_enabled: bool
    telegram_chat_id: Optional[str] = None
    whatsapp_enabled: bool
    whatsapp_to: Optional[str] = None
    webhook_enabled: bool
    webhook_url: Optional[str] = None

    on_incident_open: bool
    on_incident_close: bool
    on_sos: bool
    on_offline: bool

    channels_configured: ChannelsConfiguredView
    updated_by: Optional[int] = None
    updated_at: Optional[datetime.datetime] = None


class NotificationLogItem(BaseModel):
    """Single notification delivery record representation."""
    model_config = ConfigDict(extra="forbid")

    id: int
    mine_id: int
    event: str
    channel: str
    status: str
    attempts: int
    created_at: datetime.datetime
    sent_at: Optional[datetime.datetime] = None
    result: Optional[str] = None
    message: str


class NotificationTestResponse(BaseModel):
    """Result of triggering a test notification dispatch."""
    model_config = ConfigDict(extra="forbid")

    message: str
    ids: List[int]
