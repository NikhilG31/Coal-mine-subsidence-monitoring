"""HTTP router for ESP32 telemetry packet ingestion.

Owns the POST /api/ingest endpoint, gateway authentication, and HTTP error translations.
Must NOT execute raw database queries directly or alter packet parsing formats.
"""
import logging
import time
from typing import Dict, Optional
from fastapi import APIRouter, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.config import (
    ALLOW_UNKEYED_INGEST,
    COMMANDS_REQUIRE_HTTPS,
    TRUST_FORWARDED_PROTO,
)
from app.database import SessionLocal
from app.models.gateway import Gateway
from app.security.dependencies import security_logger
from app.services import clock
from app.services.gateway_keys import verify_key
from app.services.ingest_service import SOSHandlingError, process_telemetry_packet
from app.services.node_command_service import pending_lines_for_gateway
from app.services.packet_codec import parse_gateway_packet

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

# In-memory tracking for rate-limiting warnings (at most once/hour/gateway)
_unkeyed_warning_timestamps: Dict[str, float] = {}
_https_warning_timestamps: Dict[int, float] = {}


def _authenticate_and_ingest(
    body: bytes,
    raw_key: Optional[str],
    client_ip: str,
    is_https: bool,
) -> str:
    """Synchronous worker that parses, authenticates, persists telemetry, and returns pending commands."""
    # 1. Parsing validation (must be 38 bytes and valid structure)
    packet_data = parse_gateway_packet(body)
    gateway_mac = packet_data["gateway_mac"]
    now = clock.utcnow()

    with SessionLocal() as db:
        # 2. Gateway resolution
        gateway = db.query(Gateway).filter(Gateway.mac == gateway_mac).first()
        if not gateway:
            security_logger.warning(
                "Gateway auth failed: unknown MAC %s from IP %s",
                gateway_mac,
                client_ip,
            )
            raise HTTPException(status_code=401, detail="Gateway authentication failed")

        if not gateway.active:
            security_logger.warning(
                "Gateway auth failed: inactive gateway MAC %s from IP %s",
                gateway_mac,
                client_ip,
            )
            raise HTTPException(status_code=401, detail="Gateway authentication failed")

        if not gateway.api_key_hash or gateway.api_key_hash.startswith("placeholder_"):
            security_logger.warning(
                "Gateway auth failed: uninitialized or placeholder key hash for MAC %s from IP %s",
                gateway_mac,
                client_ip,
            )
            raise HTTPException(status_code=401, detail="Gateway authentication failed")

        # 3. Key verification
        is_unkeyed = False
        if not raw_key:
            if ALLOW_UNKEYED_INGEST:
                is_unkeyed = True
                now_mono = time.monotonic()
                last_warn = _unkeyed_warning_timestamps.get(gateway_mac)
                if last_warn is None or (now_mono - last_warn >= 3600.0):
                    security_logger.warning(
                        "ALLOW_UNKEYED_INGEST accepted unkeyed telemetry from gateway MAC %s (IP %s)",
                        gateway_mac,
                        client_ip,
                    )
                    _unkeyed_warning_timestamps[gateway_mac] = now_mono
            else:
                security_logger.warning(
                    "Gateway auth failed: missing X-Gateway-Key header for MAC %s from IP %s",
                    gateway_mac,
                    client_ip,
                )
                raise HTTPException(status_code=401, detail="Gateway authentication failed")
        else:
            if not verify_key(raw_key, gateway.api_key_hash):
                security_logger.warning(
                    "Gateway auth failed: invalid key for MAC %s from IP %s",
                    gateway_mac,
                    client_ip,
                )
                raise HTTPException(status_code=401, detail="Gateway authentication failed")

        # 4. Process telemetry and commit
        _, _, gateway_id = process_telemetry_packet(db, packet_data, now)

        # 5. Fetch pending downlink commands for nodes under this gateway
        # When request carried NO X-Gateway-Key (the ALLOW_UNKEYED_INGEST path) always return EMPTY body
        if is_unkeyed:
            command_lines = ""
        else:
            command_lines = pending_lines_for_gateway(db, gateway_id, now=now)

        # 6. Enforce HTTPS requirement on command delivery
        if COMMANDS_REQUIRE_HTTPS and not is_https:
            if command_lines:
                now_mono = time.monotonic()
                last_warn = _https_warning_timestamps.get(gateway_id)
                if last_warn is None or (now_mono - last_warn >= 3600.0):
                    security_logger.warning(
                        "Withholding node commands for gateway %d: ingest arrived over plaintext HTTP",
                        gateway_id,
                    )
                    _https_warning_timestamps[gateway_id] = now_mono
                command_lines = ""

        return command_lines


@router.post("/api/ingest")
async def ingest(
    request: Request,
    x_gateway_key: Optional[str] = Header(None),
):
    """Ingests 38-byte raw binary telemetry packets from authenticated ESP32 gateways."""
    body = await request.body()
    client_ip = request.client.host if request.client else "127.0.0.1"

    is_https = (request.url.scheme == "https")
    if not is_https and TRUST_FORWARDED_PROTO:
        if request.headers.get("x-forwarded-proto", "").lower() == "https":
            is_https = True

    try:
        command_lines = await run_in_threadpool(
            _authenticate_and_ingest,
            body,
            x_gateway_key,
            client_ip,
            is_https,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SOSHandlingError:
        raise HTTPException(status_code=503, detail="SOS handling failed")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Ingest request processing failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal ingestion error")

    return Response(content=command_lines, media_type="text/plain", status_code=200)
