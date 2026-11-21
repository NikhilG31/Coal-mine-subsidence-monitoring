"""HTTP router for ESP32 telemetry packet ingestion.

Owns the POST /api/ingest endpoint and HTTP error status translations.
Must NOT execute raw database queries or define packet parsing formats.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.ingest_service import process_telemetry_packet
from app.services.packet_codec import parse_gateway_packet

router = APIRouter(tags=["ingest"])


@router.post("/api/ingest")
async def ingest(
    request: Request,
    x_gateway_id: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Ingests 38-byte raw binary telemetry packets from ESP32 gateways."""
    body = await request.body()
    try:
        packet_data = parse_gateway_packet(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        process_telemetry_packet(db, packet_data, now)
    except LookupError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return Response(content=b"", status_code=200)
