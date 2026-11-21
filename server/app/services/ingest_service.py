"""Telemetry packet ingestion and persistence service.

Owns gateway resolution, node discovery, deduplication window checking, and reading storage.
Must NOT handle HTTP requests, access request headers directly, or import FastAPI.
"""
import datetime
from typing import Any, Dict, Tuple
from sqlalchemy.orm import Session

from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.services.hazard_classifier import (
    LEVEL_INT_TO_STR,
    classify_hazard,
)


def process_telemetry_packet(
    db: Session,
    packet_data: Dict[str, Any],
    reception_time: datetime.datetime,
) -> Tuple[bool, str]:
    """Ingests a validated packet into PostgreSQL.

    Returns:
        (is_duplicate, action_summary)

    Raises:
        LookupError: If gateway MAC is not registered or is marked inactive.
    """
    gateway_mac = packet_data["gateway_mac"]
    node_no = packet_data["node_id"]
    counter = packet_data["counter"]
    tilt_x = packet_data["tilt_x"]
    tilt_y = packet_data["tilt_y"]
    vib_rms = packet_data["vib_rms"]
    shock = packet_data["shock"]
    batt_v = packet_data["batt_v"]
    batt_pct = packet_data["batt_pct"]
    node_danger_level = packet_data["node_danger_level"]
    fast_mode = packet_data["fast_mode"]
    buzzer_on = packet_data["buzzer_on"]
    last_cmd_seq = packet_data["last_cmd_seq"]

    # 1. Resolve and verify gateway
    gateway = db.query(Gateway).filter(Gateway.mac == gateway_mac).first()
    if not gateway or not gateway.active:
        raise LookupError(f"Unknown or inactive gateway: {gateway_mac}")

    gateway.last_seen_at = reception_time

    # 2. Find or auto-create node (unplaced on initial discovery)
    node = (
        db.query(Node)
        .filter(Node.gateway_id == gateway.id, Node.node_no == node_no)
        .first()
    )
    if not node:
        node = Node(
            gateway_id=gateway.id,
            node_no=node_no,
            tunnel_id=None,
            lat=None,
            lon=None,
            status="normal",
            battery_pct=batt_pct,
            fast_mode=fast_mode,
            buzzer_on=buzzer_on,
            last_cmd_seq=last_cmd_seq,
            last_seen_at=reception_time,
        )
        db.add(node)
        db.flush()

    # 3. Independent server-side hazard classification
    computed_level = classify_hazard(tilt_x, tilt_y, vib_rms)
    status_str = LEVEL_INT_TO_STR[computed_level]

    # 4. Deduplication rule for boot_id = 0:
    # A reading is classified as duplicate ONLY IF a row exists with same (node_id, boot_id=0, counter)
    # AND server reception delta |now - existing.ts| < 4.0 seconds.
    last_reading = (
        db.query(Reading)
        .filter(
            Reading.node_id == node.id,
            Reading.boot_id == 0,
            Reading.counter == counter,
        )
        .order_by(Reading.ts.desc())
        .first()
    )

    is_duplicate = False
    if last_reading and last_reading.ts is not None:
        delta = abs((reception_time - last_reading.ts).total_seconds())
        if delta < 4.0:
            is_duplicate = True

    # 5. Update latest node metadata
    node.last_seen_at = reception_time
    node.battery_pct = batt_pct
    node.status = status_str
    node.fast_mode = fast_mode
    node.buzzer_on = buzzer_on
    node.last_cmd_seq = last_cmd_seq

    if is_duplicate:
        db.commit()
        return True, "duplicate_skipped"

    # 6. Insert new reading row
    reading = Reading(
        node_id=node.id,
        ts=reception_time,
        boot_id=0,
        counter=counter,
        tilt_x=tilt_x,
        tilt_y=tilt_y,
        vib_rms=vib_rms,
        shock=shock,
        batt_v=batt_v,
        batt_pct=batt_pct,
        node_level=node_danger_level,
        level=computed_level,
    )
    db.add(reading)
    db.commit()

    return False, "reading_inserted"
