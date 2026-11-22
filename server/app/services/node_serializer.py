"""Telemetry node serializer for client representations.

Owns formatting Node and Reading model records into superior and worker view shapes.
Must NOT execute database queries or import FastAPI routing constructs.
"""
from typing import Any, Dict, Optional
from app.models.node import Node
from app.models.reading import Reading
from app.services.hazard_classifier import (
    LEVEL_COLORS,
    LEVEL_NORMAL,
    get_hazard_details,
)
from app.services.node_placement import is_placed


def serialize_node(
    node: Node,
    latest_reading: Optional[Reading],
    now_ts: float,
    offline_after_s: int = 35,
) -> Dict[str, Any]:
    """Serializes a Node record into the full dictionary representation for superiors."""
    mac = node.gateway.mac if node.gateway else "UNKNOWN"
    gw_name = node.gateway.name if node.gateway else "Gateway"

    if node.last_seen_at:
        age_s = round(now_ts - node.last_seen_at.timestamp(), 1)
    else:
        age_s = 999.0
    online = age_s <= offline_after_s

    placed = is_placed(node)
    if node.tunnel:
        loc_name = node.tunnel.name
    elif placed:
        loc_name = f"Node {node.node_no}"
    else:
        loc_name = f"Node {node.node_no} (Unplaced)"

    status = node.status or LEVEL_NORMAL
    batt_pct = float(node.battery_pct) if node.battery_pct is not None else 100.0

    if latest_reading:
        tx = round(float(latest_reading.tilt_x), 3)
        ty = round(float(latest_reading.tilt_y), 3)
        vm = round(float(latest_reading.vib_rms), 4)
        vd = bool(latest_reading.shock)
        bv = round(float(latest_reading.batt_v), 3)
        cnt = latest_reading.counter
    else:
        tx = 0.0
        ty = 0.0
        vm = 0.0
        vd = False
        bv = 4.0
        cnt = 0

    analysis = get_hazard_details(
        tilt_x=tx,
        tilt_y=ty,
        vib_rms=vm,
        shock=vd,
        batt_v=bv,
        batt_pct=batt_pct,
    )

    return {
        "id": node.id,
        "mine_id": node.gateway.mine_id if node.gateway else None,
        "gateway_mac_address": mac,
        "gateway": gw_name,
        "node_id": node.node_no,
        "tunnel_id": node.tunnel_id,
        "counter": cnt,
        "tilt_x": tx,
        "tilt_y": ty,
        "vibration_magnitude": vm,
        "vibration_detected": vd,
        "battery_voltage": bv,
        "battery_percentage": round(batt_pct, 1),
        "status": status,
        "score": analysis["score"],
        "reasons": analysis["reasons"],
        "color": LEVEL_COLORS.get(status, LEVEL_COLORS[LEVEL_NORMAL]),
        "low_battery": batt_pct <= 20.0,
        "age_s": age_s,
        "online": online,
        "placed": placed,
        "latitude": node.lat,
        "longitude": node.lon,
        "location_name": loc_name,
    }


def serialize_worker_node(
    node: Node,
    latest_reading: Optional[Reading],
    now_ts: float,
    offline_after_s: int = 35,
) -> Dict[str, Any]:
    """Serializes a Node record into the restricted dictionary representation for workers.

    Spec B6: Must ONLY carry node_id, location_name, latitude, longitude, status,
    color, tilt_x, tilt_y, vibration_magnitude, vibration_detected, battery_percentage,
    low_battery, online, age_s.
    Must NOT carry gateway MAC or name, counter, score, reasons, or placed_by.
    """
    full = serialize_node(node, latest_reading, now_ts, offline_after_s)
    return {
        "node_id": full["node_id"],
        "location_name": full["location_name"],
        "latitude": full["latitude"],
        "longitude": full["longitude"],
        "status": full["status"],
        "color": full["color"],
        "tilt_x": full["tilt_x"],
        "tilt_y": full["tilt_y"],
        "vibration_magnitude": full["vibration_magnitude"],
        "vibration_detected": full["vibration_detected"],
        "battery_percentage": full["battery_percentage"],
        "low_battery": full["low_battery"],
        "online": full["online"],
        "age_s": full["age_s"],
    }
