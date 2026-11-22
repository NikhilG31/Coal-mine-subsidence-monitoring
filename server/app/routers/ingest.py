"""HTTP router for ESP32 telemetry packet ingestion.

Owns the POST /api/ingest endpoint, gateway authentication, and HTTP error translations.
Must NOT execute raw database queries directly or alter packet parsing formats.
"""
import datetime
import time
from typing import Dict, Optional
from fastapi import APIRouter, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.config import ALLOW_UNKEYED_INGEST
from app.database import SessionLocal
from app.models.gateway import Gateway
from app.security.dependencies import security_logger
from app.services import clock
from app.services.gateway_keys import verify_key
from app.services.ingest_service import process_telemetry_packet
from app.services.packet_codec import parse_gateway_packet

router = APIRouter(tags=["ingest"])

# In-memory tracking for rate-limiting ALLOW_UNKEYED_INGEST warning (at most once/hour/gateway)
_unkeyed_warning_timestamps: Dict[str, float] = {}


def _authenticate_and_ingest(
    body: bytes,
    raw_key: Optional[str],
    client_ip: str,
) -> None:
    """Synchronous worker that parses, authenticates, and persists telemetry in a dedicated session."""
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
        if not raw_key:
            if ALLOW_UNKEYED_INGEST:
                now_mono = time.monotonic()
                last_warn = _unkeyed_warning_timestamps.get(gateway_mac, 0.0)
                if now_mono - last_warn >= 3600.0:
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
        process_telemetry_packet(db, packet_data, now)


@router.post("/api/ingest")
async def ingest(
    request: Request,
    x_gateway_key: Optional[str] = Header(None),
):
    """Ingests 38-byte raw binary telemetry packets from authenticated ESP32 gateways."""
    body = await request.body()
    client_ip = request.client.host if request.client else "127.0.0.1"

    try:
        await run_in_threadpool(
            _authenticate_and_ingest,
            body,
            x_gateway_key,
            client_ip,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal ingestion error")

    return Response(content=b"", status_code=200)
