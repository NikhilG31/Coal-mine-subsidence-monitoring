"""Notification delivery adapters for Telegram, WhatsApp, and Webhooks.

Owns HTTP dispatch, payload signing, credential isolation, and outcome sanitization across channels.
Must NOT interact with database sessions directly or leak authentication secrets in logs or errors.
"""
from dataclasses import dataclass
import datetime
import hashlib
import hmac
import json
import logging
from typing import Any, Callable, Dict, List, Optional
import httpx

from app.config import (
    NOTIFY_HTTP_TIMEOUT_S,
    TELEGRAM_BOT_TOKEN,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
    WEBHOOK_SIGNING_SECRET,
)
from app.services.webhook_guard import validate_webhook_url

logger = logging.getLogger(__name__)

__all__ = ["SendResult", "send", "verify_webhook_signature"]


@dataclass(frozen=True)
class SendResult:
    """Outcome of a single notification delivery attempt."""
    ok: bool
    retryable: bool
    detail: str


def _scrub_secrets(text: str) -> str:
    """Removes sensitive credentials and secrets from logs and diagnostic texts."""
    for secret in (
        TELEGRAM_BOT_TOKEN,
        TWILIO_ACCOUNT_SID,
        TWILIO_AUTH_TOKEN,
        WEBHOOK_SIGNING_SECRET,
    ):
        if secret and secret.strip():
            text = text.replace(secret.strip(), "[REDACTED]")
    return text


def _send_telegram(
    destination: str,
    message: str,
    transport: Optional[httpx.BaseTransport] = None,
) -> SendResult:
    """Dispatches plain text alert to Telegram bot sendMessage endpoint."""
    if not TELEGRAM_BOT_TOKEN:
        return SendResult(ok=False, retryable=False, detail="Telegram not configured")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": destination, "text": message}

    try:
        with httpx.Client(
            timeout=NOTIFY_HTTP_TIMEOUT_S,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = client.post(url, json=payload)

        # Telegram redirect check
        if 300 <= resp.status_code < 400:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code} redirect rejected")

        if 200 <= resp.status_code < 300:
            return SendResult(ok=True, retryable=False, detail=f"HTTP {resp.status_code}")

        if resp.status_code == 429:
            return SendResult(ok=False, retryable=True, detail="HTTP 429")

        if 400 <= resp.status_code < 500:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code}")

        return SendResult(ok=False, retryable=True, detail=f"HTTP {resp.status_code}")

    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        return SendResult(ok=False, retryable=True, detail=exc.__class__.__name__)
    except Exception as exc:
        sanitized = _scrub_secrets(exc.__class__.__name__)[:200]
        return SendResult(ok=False, retryable=False, detail=sanitized)


def _send_whatsapp(
    destination: str,
    message: str,
    transport: Optional[httpx.BaseTransport] = None,
) -> SendResult:
    """Dispatches WhatsApp alert via Twilio Messages REST API."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_FROM):
        return SendResult(ok=False, retryable=False, detail="WhatsApp not configured")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    from_addr = TWILIO_WHATSAPP_FROM.strip()
    if not from_addr.startswith("whatsapp:"):
        from_addr = f"whatsapp:{from_addr}"

    to_addr = destination.strip()
    if not to_addr.startswith("whatsapp:"):
        to_addr = f"whatsapp:{to_addr}"

    data = {
        "From": from_addr,
        "To": to_addr,
        "Body": message,
    }

    try:
        with httpx.Client(
            timeout=NOTIFY_HTTP_TIMEOUT_S,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = client.post(
                url,
                data=data,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )

        if 300 <= resp.status_code < 400:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code} redirect rejected")

        if 200 <= resp.status_code < 300:
            return SendResult(ok=True, retryable=False, detail=f"HTTP {resp.status_code}")

        if resp.status_code == 429:
            return SendResult(ok=False, retryable=True, detail="HTTP 429")

        if 400 <= resp.status_code < 500:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code}")

        return SendResult(ok=False, retryable=True, detail=f"HTTP {resp.status_code}")

    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        return SendResult(ok=False, retryable=True, detail=exc.__class__.__name__)
    except Exception as exc:
        sanitized = _scrub_secrets(exc.__class__.__name__)[:200]
        return SendResult(ok=False, retryable=False, detail=sanitized)


def verify_webhook_signature(
    secret: str,
    timestamp: str,
    raw_body: bytes,
    signature_hex: str,
) -> bool:
    """Test helper & documentation reference to verify X-CMB-Signature HMAC-SHA256."""
    data_to_sign = f"{timestamp}.".encode("utf-8") + raw_body
    computed = hmac.new(secret.encode("utf-8"), data_to_sign, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed.lower(), signature_hex.lower())


def _send_webhook(
    destination: str,
    message: str,
    now: datetime.datetime,
    extra_payload: Optional[Dict[str, Any]] = None,
    transport: Optional[httpx.BaseTransport] = None,
    resolver: Optional[Callable[[str], List[str]]] = None,
) -> SendResult:
    """Dispatches JSON payload with HMAC-SHA256 signature to customer webhook endpoint."""
    if not WEBHOOK_SIGNING_SECRET:
        return SendResult(ok=False, retryable=False, detail="Webhook not configured")

    # SSRF guard validation immediately prior to dispatch
    try:
        validate_webhook_url(destination, resolver=resolver)
    except Exception as exc:
        logger.warning("SSRF validation failed for webhook destination: %s", exc)
        return SendResult(ok=False, retryable=False, detail="SSRF validation failed")

    extra = extra_payload or {}
    payload = {
        "event": extra.get("event", "notification"),
        "mine": extra.get("mine", ""),
        "text": message,
        "time": now.isoformat(),
        "id": extra.get("id"),
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp_str = str(int(now.timestamp()))
    data_to_sign = f"{timestamp_str}.".encode("utf-8") + raw_body
    signature = hmac.new(
        WEBHOOK_SIGNING_SECRET.encode("utf-8"),
        data_to_sign,
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-CMB-Timestamp": timestamp_str,
        "X-CMB-Signature": signature,
    }

    try:
        with httpx.Client(
            timeout=NOTIFY_HTTP_TIMEOUT_S,
            follow_redirects=False,
            transport=transport,
        ) as client:
            resp = client.post(destination, content=raw_body, headers=headers)

        if 300 <= resp.status_code < 400:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code} redirect rejected")

        if 200 <= resp.status_code < 300:
            return SendResult(ok=True, retryable=False, detail=f"HTTP {resp.status_code}")

        if 400 <= resp.status_code < 500:
            return SendResult(ok=False, retryable=False, detail=f"HTTP {resp.status_code}")

        return SendResult(ok=False, retryable=True, detail=f"HTTP {resp.status_code}")

    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        return SendResult(ok=False, retryable=True, detail=exc.__class__.__name__)
    except Exception as exc:
        sanitized = _scrub_secrets(exc.__class__.__name__)[:200]
        return SendResult(ok=False, retryable=False, detail=sanitized)


def send(
    channel: str,
    destination: str,
    message: str,
    now: datetime.datetime,
    transport: Optional[httpx.BaseTransport] = None,
    resolver: Optional[Callable[[str], List[str]]] = None,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> SendResult:
    """Dispatches notification across specified channel adapter with sanitised outcome detail."""
    if channel == "telegram":
        return _send_telegram(destination, message, transport=transport)
    elif channel == "whatsapp":
        return _send_whatsapp(destination, message, transport=transport)
    elif channel == "webhook":
        return _send_webhook(
            destination,
            message,
            now,
            extra_payload=extra_payload,
            transport=transport,
            resolver=resolver,
        )
    return SendResult(ok=False, retryable=False, detail=f"Unknown channel '{channel}'")
