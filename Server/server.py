"""FastAPI Server for Coal Mine Subsidence Monitoring System (PostgreSQL).

Receives 38-byte binary telemetry packets from ESP32 Gateways, performs
deduplication and independent classification, records readings into PostgreSQL,
and serves telemetry to the Leaflet web dashboard.
"""
import datetime
import io
import json
import math
import os
import struct
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from analyzer import Analyzer, LEVEL_COLORS, LEVEL_NORMAL
from database import SessionLocal, get_db
from models import Company, Gateway, Mine, Node, Reading, Tunnel
from simulator import Simulator, pack_payload

BASE = Path(__file__).parent
PAYLOAD_FMT = "<6sBIfffBffBBBBH"
PAYLOAD_SIZE = struct.calcsize(PAYLOAD_FMT)
assert PAYLOAD_SIZE == 38, f"PAYLOAD_FMT must be 38 bytes, got {PAYLOAD_SIZE}"

OFFLINE_AFTER_S = int(os.getenv("OFFLINE_AFTER_S", "35"))
LEVEL_INT_TO_STR = {0: "normal", 1: "watch", 2: "warning", 3: "critical"}
LEVEL_STR_TO_INT = {v: k for k, v in LEVEL_INT_TO_STR.items()}

app = FastAPI(title="Coal Mine Subsidence Monitoring System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

analyzer = Analyzer()
simulator = Simulator()


def norm_mac(value: Optional[str]) -> str:
    """Canonical gateway MAC: 'AA:BB:CC:DD:EE:FF'."""
    h = "".join(c for c in str(value or "").upper() if c in "0123456789ABCDEF")
    if len(h) != 12:
        return "UNKNOWN"
    return ":".join(h[i : i + 2] for i in range(0, 12, 2))


# ==========================================
# SENSOR INGESTION ENDPOINT (POST /api/ingest)
# ==========================================

@app.post("/api/ingest")
async def ingest(
    request: Request,
    x_gateway_id: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Ingests 38-byte raw binary telemetry packets from ESP32 gateway.

    Format: <6sBIfffBffBBBBH (little-endian, no padding, 38 bytes).
    """
    body = await request.body()
    # Spec: Accept EXACTLY 38 bytes. Any other length returns 400.
    if len(body) != PAYLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"Expected body of exactly {PAYLOAD_SIZE} bytes, got {len(body)}",
        )

    (
        mac_bytes,
        node_id,
        counter,
        tilt_x,
        tilt_y,
        vib_magnitude,
        vib_detected,
        batt_v,
        batt_pct,
        sos_active,
        node_danger_level,
        fast_mode,
        buzzer_on,
        last_cmd_seq,
    ) = struct.unpack(PAYLOAD_FMT, body)

    # Validate numeric floats
    for name, val in [("tilt_x", tilt_x), ("tilt_y", tilt_y), ("vib_rms", vib_magnitude), ("batt_v", batt_v), ("batt_pct", batt_pct)]:
        if val is None or not math.isfinite(val):
            raise HTTPException(status_code=400, detail=f"Metric '{name}' is NaN or infinite")

    now = datetime.datetime.now(datetime.timezone.utc)
    now_ts = now.timestamp()
    gateway_mac = norm_mac(":".join(f"{b:02X}" for b in mac_bytes))

    # Gateway check (interim): keep existing X-Gateway-Id header behavior.
    # Resolve gateway row by MAC in the body. Reject unknown or inactive gateway.
    gateway = db.query(Gateway).filter(Gateway.mac == gateway_mac).first()
    if not gateway or not gateway.active:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown or inactive gateway: {gateway_mac}",
        )
    gateway.last_seen_at = now

    # Find or auto-create node by (gateway_id, node_id -> node_no)
    node = (
        db.query(Node)
        .filter(Node.gateway_id == gateway.id, Node.node_no == node_id)
        .first()
    )
    if not node:
        node = Node(
            gateway_id=gateway.id,
            node_no=node_id,
            tunnel_id=None,
            lat=None,
            lon=None,
            status="normal",
            battery_pct=batt_pct,
            fast_mode=bool(fast_mode),
            buzzer_on=bool(buzzer_on),
            last_cmd_seq=last_cmd_seq,
            last_seen_at=now,
        )
        db.add(node)
        db.flush()

    # Server classification: compute level independently using analyzer logic
    analysis = analyzer.analyze(
        {
            "tilt_x": tilt_x,
            "tilt_y": tilt_y,
            "vibration_magnitude": vib_magnitude,
            "vibration_detected": bool(vib_detected),
            "battery_voltage": batt_v,
            "battery_percentage": batt_pct,
        }
    )
    status_str = analysis["status"]
    computed_level = LEVEL_STR_TO_INT.get(status_str, 0)

    # Dedup while boot_id = 0:
    # A reading is a duplicate ONLY IF a row exists with same node_id, boot_id = 0
    # and counter AND its ts is within 4 seconds of the new reading's ts.
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
        delta = abs((now - last_reading.ts).total_seconds())
        if delta < 4.0:
            is_duplicate = True

    if is_duplicate:
        # Duplicate within 4s: do not insert, still return 200 with empty body
        node.last_seen_at = now
        node.battery_pct = batt_pct
        node.status = status_str
        node.fast_mode = bool(fast_mode)
        node.buzzer_on = bool(buzzer_on)
        node.last_cmd_seq = last_cmd_seq
        db.commit()
        return Response(content=b"", status_code=200)

    # Insert new reading
    reading = Reading(
        node_id=node.id,
        ts=now,
        boot_id=0,
        counter=counter,
        tilt_x=tilt_x,
        tilt_y=tilt_y,
        vib_rms=vib_magnitude,
        shock=bool(vib_detected),
        batt_v=batt_v,
        batt_pct=batt_pct,
        node_level=node_danger_level,
        level=computed_level,
    )
    db.add(reading)

    # Update node
    node.last_seen_at = now
    node.battery_pct = batt_pct
    node.status = status_str
    node.fast_mode = bool(fast_mode)
    node.buzzer_on = bool(buzzer_on)
    node.last_cmd_seq = last_cmd_seq
    db.commit()

    # Success: HTTP 200 with EMPTY body
    return Response(content=b"", status_code=200)


# ==========================================
# DASHBOARD READ ENDPOINTS
# ==========================================

def serialize_node_dict(node: Node, latest_reading: Optional[Reading], now_ts: float) -> dict:
    """Serializes a node into the exact dictionary shape consumed by the web dashboard."""
    mac = node.gateway.mac if node.gateway else "UNKNOWN"
    gw_name = node.gateway.name if node.gateway else "Gateway"

    if node.last_seen_at:
        age_s = round(now_ts - node.last_seen_at.timestamp(), 1)
    else:
        age_s = 999.0
    online = age_s <= OFFLINE_AFTER_S

    placed = node.lat is not None and node.lon is not None
    if node.tunnel:
        loc_name = node.tunnel.name
    elif placed:
        loc_name = f"Node {node.node_no}"
    else:
        loc_name = f"Node {node.node_no} (Unplaced)"

    status = node.status or LEVEL_NORMAL
    batt_pct = node.battery_pct if node.battery_pct is not None else 100.0

    if latest_reading:
        tx = round(latest_reading.tilt_x, 3)
        ty = round(latest_reading.tilt_y, 3)
        vm = round(latest_reading.vib_rms, 4)
        vd = bool(latest_reading.shock)
        bv = round(latest_reading.batt_v, 3)
        cnt = latest_reading.counter
    else:
        tx = 0.0
        ty = 0.0
        vm = 0.0
        vd = False
        bv = 4.0
        cnt = 0

    analysis = analyzer.analyze(
        {
            "tilt_x": tx,
            "tilt_y": ty,
            "vibration_magnitude": vm,
            "vibration_detected": vd,
            "battery_voltage": bv,
            "battery_percentage": batt_pct,
        }
    )

    return {
        "gateway_mac_address": mac,
        "gateway": gw_name,
        "node_id": node.node_no,
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


@app.get("/api/nodes")
def api_nodes(gateway: Optional[str] = None, db: Session = Depends(get_db)):
    """Returns latest telemetry and placement info for all nodes."""
    now_ts = time.time()
    query = db.query(Node)
    if gateway:
        query = query.join(Gateway).filter(Gateway.mac == norm_mac(gateway))
    nodes = query.order_by(Node.gateway_id, Node.node_no).all()

    # Efficiently load latest reading per node using PostgreSQL DISTINCT ON
    node_ids = [n.id for n in nodes]
    latest_map = {}
    if node_ids:
        latest_readings = (
            db.query(Reading)
            .distinct(Reading.node_id)
            .filter(Reading.node_id.in_(node_ids))
            .order_by(Reading.node_id, Reading.ts.desc())
            .all()
        )
        latest_map = {r.node_id: r for r in latest_readings}

    nodes_list = [serialize_node_dict(n, latest_map.get(n.id), now_ts) for n in nodes]
    return {"now": now_ts, "nodes": nodes_list}


@app.get("/api/gateways")
def api_gateways(db: Session = Depends(get_db)):
    """Returns the gateway hierarchy and node inventory."""
    now_ts = time.time()
    nodes = db.query(Node).order_by(Node.gateway_id, Node.node_no).all()
    node_ids = [n.id for n in nodes]
    latest_map = {}
    if node_ids:
        latest_readings = (
            db.query(Reading)
            .distinct(Reading.node_id)
            .filter(Reading.node_id.in_(node_ids))
            .order_by(Reading.node_id, Reading.ts.desc())
            .all()
        )
        latest_map = {r.node_id: r for r in latest_readings}

    nodes_list = [serialize_node_dict(n, latest_map.get(n.id), now_ts) for n in nodes]

    gateways = db.query(Gateway).all()
    tree = {}
    for gw in gateways:
        tree[gw.mac] = {
            "gateway_mac_address": gw.mac,
            "gateway_name": gw.name,
            "node_count": 0,
            "online_nodes": 0,
            "placed_nodes": 0,
            "worst_status": LEVEL_NORMAL,
            "last_seen_age_s": round(now_ts - gw.last_seen_at.timestamp(), 1) if gw.last_seen_at else None,
            "nodes": [],
        }

    status_ranks = {"normal": 0, "watch": 1, "warning": 2, "critical": 3}
    for n in nodes_list:
        mac = n["gateway_mac_address"]
        if mac not in tree:
            tree[mac] = {
                "gateway_mac_address": mac,
                "gateway_name": n.get("gateway") or f"Gateway {mac[-8:]}",
                "node_count": 0,
                "online_nodes": 0,
                "placed_nodes": 0,
                "worst_status": LEVEL_NORMAL,
                "last_seen_age_s": None,
                "nodes": [],
            }
        g = tree[mac]
        g["nodes"].append(n)
        g["node_count"] += 1
        g["online_nodes"] += int(n["online"])
        g["placed_nodes"] += int(n["placed"])
        if g["last_seen_age_s"] is None or n["age_s"] < g["last_seen_age_s"]:
            g["last_seen_age_s"] = n["age_s"]

    for g in tree.values():
        g["online"] = g["online_nodes"] > 0
        if g["nodes"]:
            worst = max(g["nodes"], key=lambda node: status_ranks.get(node["status"], 0))
            g["worst_status"] = worst["status"]

    return {"now": now_ts, "gateways": list(tree.values())}


@app.get("/api/history")
def api_history(
    gateway: Optional[str] = None,
    node_id: Optional[int] = None,
    limit: int = Query(60, ge=1, le=5000),
    only_alerts: bool = False,
    db: Session = Depends(get_db),
):
    """Returns telemetry points ordered by time for Chart.js inspector."""
    q = db.query(Reading).join(Node).join(Gateway)

    if gateway:
        q = q.filter(Gateway.mac == norm_mac(gateway))
    if node_id is not None:
        q = q.filter(Node.node_no == node_id)
    if only_alerts:
        q = q.filter(Reading.level > 0)

    rows = q.order_by(Reading.id.desc()).limit(limit).all()

    out = []
    for r in reversed(rows):
        st = LEVEL_INT_TO_STR.get(r.level, "normal")
        ana = analyzer.analyze(
            {
                "tilt_x": r.tilt_x,
                "tilt_y": r.tilt_y,
                "vibration_magnitude": r.vib_rms,
                "vibration_detected": r.shock,
                "battery_voltage": r.batt_v,
                "battery_percentage": r.batt_pct,
            }
        )
        out.append(
            {
                "id": r.id,
                "ts": r.ts.timestamp(),
                "gateway": r.node.gateway.name if r.node.gateway else "Gateway",
                "gateway_mac_address": r.node.gateway.mac if r.node.gateway else "UNKNOWN",
                "node_id": r.node.node_no,
                "counter": r.counter,
                "tilt_x": round(r.tilt_x, 3),
                "tilt_y": round(r.tilt_y, 3),
                "vibration_magnitude": round(r.vib_rms, 4),
                "vibration_detected": bool(r.shock),
                "battery_voltage": round(r.batt_v, 3),
                "battery_percentage": round(r.batt_pct, 1),
                "status": st,
                "score": ana["score"],
                "reasons": ana["reasons"],
                "color": LEVEL_COLORS.get(st, LEVEL_COLORS[LEVEL_NORMAL]),
                "low_battery": r.batt_pct <= 20.0,
            }
        )

    return {"readings": out}


# ==========================================
# NODE PLACEMENT & MAP ENDPOINTS
# ==========================================

@app.get("/api/placements")
def api_placements(db: Session = Depends(get_db)):
    """Returns placed nodes and their coordinates."""
    placed = (
        db.query(Node)
        .filter(Node.lat.isnot(None), Node.lon.isnot(None))
        .all()
    )
    res = []
    for n in placed:
        res.append(
            {
                "gateway_mac_address": n.gateway.mac,
                "node_id": n.node_no,
                "location_name": n.tunnel.name if n.tunnel else f"Node {n.node_no}",
                "latitude": n.lat,
                "longitude": n.lon,
                "placed_at": n.placed_at.timestamp() if n.placed_at else time.time(),
                "placed_by": n.placed_by,
            }
        )
    return {"placements": res}


@app.post("/api/placements")
async def api_save_placement(request: Request, db: Session = Depends(get_db)):
    """Saves geographic coordinates for a node."""
    data = await request.json()
    mac = norm_mac(data.get("gateway_mac_address"))
    node_no = int(data.get("node_id", 0))
    lat = float(data.get("latitude", 0.0))
    lng = float(data.get("longitude", 0.0))

    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid latitude/longitude coordinates")

    gateway = db.query(Gateway).filter(Gateway.mac == mac).first()
    if not gateway:
        raise HTTPException(status_code=404, detail="Gateway not found")

    node = (
        db.query(Node)
        .filter(Node.gateway_id == gateway.id, Node.node_no == node_no)
        .first()
    )
    if not node:
        node = Node(gateway_id=gateway.id, node_no=node_no)
        db.add(node)

    node.lat = lat
    node.lon = lng
    node.placed_at = datetime.datetime.now(datetime.timezone.utc)

    # Link to mine's first tunnel if tunnel not assigned
    if node.tunnel_id is None:
        first_tunnel = db.query(Tunnel).filter(Tunnel.mine_id == gateway.mine_id).first()
        if first_tunnel:
            node.tunnel_id = first_tunnel.id

    db.commit()
    return {"message": "Node location saved", "node_id": node_no, "gateway_mac": mac}


@app.delete("/api/placements/{gateway}/{node_id}")
def api_delete_placement(gateway: str, node_id: int, db: Session = Depends(get_db)):
    """Unplaces a node from the map."""
    mac = norm_mac(gateway)
    gw = db.query(Gateway).filter(Gateway.mac == mac).first()
    if gw:
        node = db.query(Node).filter(Node.gateway_id == gw.id, Node.node_no == node_id).first()
        if node:
            node.lat = None
            node.lon = None
            node.tunnel_id = None
            node.placed_at = None
            db.commit()
    return {"message": "Node unplaced from map"}


@app.get("/api/unplaced")
def api_unplaced(db: Session = Depends(get_db)):
    """Returns nodes reporting data that have not yet been placed on the surface map."""
    now_ts = time.time()
    unplaced_nodes = (
        db.query(Node)
        .filter((Node.lat.is_(None)) | (Node.lon.is_(None)) | (Node.tunnel_id.is_(None)))
        .all()
    )
    node_ids = [n.id for n in unplaced_nodes]
    latest_map = {}
    if node_ids:
        latest_readings = (
            db.query(Reading)
            .distinct(Reading.node_id)
            .filter(Reading.node_id.in_(node_ids))
            .order_by(Reading.node_id, Reading.ts.desc())
            .all()
        )
        latest_map = {r.node_id: r for r in latest_readings}

    serialized = [serialize_node_dict(n, latest_map.get(n.id), now_ts) for n in unplaced_nodes]
    return {"unplaced": serialized}


# ==========================================
# AUTH ENDPOINTS (STEP 1: PERMISSIVE COMPATIBILITY)
# ==========================================

@app.get("/api/auth/me")
def api_me():
    return {
        "user": {"id": 1, "username": "manager", "role": "manager", "email": "manager@coalmine.gov.in"},
        "allowed_gateways": None,
    }


@app.post("/api/auth/login")
def api_login():
    return {
        "token": "step1-dev-token",
        "user": {"id": 1, "username": "manager", "role": "manager"},
    }


@app.post("/api/auth/logout")
def api_logout():
    return {"message": "Logged out"}


@app.get("/api/users")
def api_users():
    return {"users": [{"id": 1, "username": "manager", "role": "manager"}]}


@app.post("/api/users")
def api_create_user():
    return {"message": "User added successfully"}


# ==========================================
# ALERTS & NOTIFICATIONS (STEP 1: EMPTY STUBS)
# ==========================================

@app.get("/api/alerts")
def api_alerts():
    return {"alerts": []}


@app.post("/api/alerts/{alert_id}/ack")
def api_ack_alert(alert_id: int):
    return {"message": "Alert acknowledged", "id": alert_id}


@app.get("/api/notifications/config")
def api_notif_config():
    return {"config": {}}


@app.post("/api/notifications/config")
def api_save_notif_config():
    return {"message": "Notification configurations updated"}


# ==========================================
# SIMULATOR & DEMO CONTROLS
# ==========================================

@app.post("/api/demo/seed")
def api_demo_seed():
    import dev_seed
    dev_seed.dev_seed()
    return {"message": "Demo coal mine datasets seeded (Jharia & Raniganj fields ready)"}


@app.post("/api/demo/step")
def api_demo_step():
    count = simulator.step()
    return {"message": "Single simulated step generated", "count": count}


@app.post("/api/demo/toggle-live")
async def api_demo_toggle(request: Request):
    data = await request.json() if (await request.body()) else {}
    enable = data.get("enable", not simulator.is_running)
    if enable:
        simulator.start_loop()
    else:
        simulator.stop_loop()
    return {"running": simulator.is_running}


@app.get("/api/demo/status")
def api_demo_status():
    return {"running": simulator.is_running}


@app.post("/api/demo/trigger-hazard")
async def api_trigger_hazard(request: Request):
    data = await request.json()
    mac = norm_mac(data.get("gateway_mac_address", "A4:C1:38:90:12:01"))
    node_id = int(data.get("node_id", 3))
    level = str(data.get("level", "critical")).lower()
    simulator.trigger_hazard(mac, node_id, level)
    simulator.step()
    return {"message": f"Hazard override '{level}' triggered on Node {node_id} ({mac})"}


# ==========================================
# EXCEL EXPORT ENDPOINT (.xlsx)
# ==========================================

XLSX_HEADERS = [
    "Timestamp (ISO)",
    "Epoch Time",
    "Gateway Name",
    "Gateway MAC",
    "Node ID",
    "Packet Counter",
    "Tilt X (°)",
    "Tilt Y (°)",
    "Vibration RMS (g)",
    "SW160 Shock Detected",
    "Battery (V)",
    "Battery (%)",
    "Danger Level",
    "Hazard Score (0-1)",
    "Diagnostic Reasons",
]
XLSX_WIDTHS = [22, 14, 25, 20, 10, 14, 12, 12, 18, 22, 14, 14, 15, 18, 48]


@app.get("/api/export.xlsx")
def export_xlsx(
    gateway: Optional[str] = None,
    node_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Excel workbook export with formatting."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    q = db.query(Reading).join(Node).join(Gateway)
    if gateway:
        q = q.filter(Gateway.mac == norm_mac(gateway))
    if node_id is not None:
        q = q.filter(Node.node_no == node_id)

    rows = q.order_by(Reading.id.asc()).all()

    wb = Workbook()
    ws_all = wb.active
    ws_all.title = "All Telemetry Readings"

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1A1108", end_color="1A1108", fill_type="solid")

    def style_headers(ws):
        ws.append(XLSX_HEADERS)
        for col_idx, cell in enumerate(ws[1], 1):
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.freeze_panes = "A2"
        for i, w in enumerate(XLSX_WIDTHS):
            col_letter = chr(ord("A") + i) if i < 26 else f"A{chr(ord('A') + i - 26)}"
            ws.column_dimensions[col_letter].width = w

    style_headers(ws_all)
    sheets_by_node = {}

    for r in rows:
        st = LEVEL_INT_TO_STR.get(r.level, "normal")
        ana = analyzer.analyze(
            {
                "tilt_x": r.tilt_x,
                "tilt_y": r.tilt_y,
                "vibration_magnitude": r.vib_rms,
                "vibration_detected": r.shock,
                "battery_voltage": r.batt_v,
                "battery_percentage": r.batt_pct,
            }
        )
        line = [
            r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            round(r.ts.timestamp(), 3),
            r.node.gateway.name if r.node.gateway else "Gateway",
            r.node.gateway.mac if r.node.gateway else "UNKNOWN",
            r.node.node_no,
            r.counter,
            round(r.tilt_x, 3),
            round(r.tilt_y, 3),
            round(r.vib_rms, 4),
            "YES (Triggered)" if r.shock else "NO",
            round(r.batt_v, 3),
            round(r.batt_pct, 1),
            st.upper(),
            round(ana["score"], 3),
            "; ".join(ana["reasons"]),
        ]
        ws_all.append(line)

        sheet_key = (r.node.gateway.mac, r.node.node_no)
        ws_node = sheets_by_node.get(sheet_key)
        if ws_node is None:
            tail = (r.node.gateway.mac or "UNKNOWN").replace(":", "")[-6:]
            tab_name = f"GW {tail} Node {r.node.node_no}"[:31]
            ws_node = sheets_by_node[sheet_key] = wb.create_sheet(title=tab_name)
            style_headers(ws_node)
        ws_node.append(line)

    for sheet in wb.worksheets:
        sheet.auto_filter.ref = sheet.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    filename = f"mine_subsidence_telemetry_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ==========================================
# HEALTH & STATIC FILES
# ==========================================

@app.get("/api/health")
def api_health():
    return {
        "status": "healthy",
        "system": "Coal Mine Subsidence Monitoring Server",
        "database": "PostgreSQL",
        "ml_source": analyzer.source,
        "payload_size": PAYLOAD_SIZE,
        "simulator_active": simulator.is_running,
    }


@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
