"""HTTP router for Excel telemetry log export.

Owns GET /api/export.xlsx with superior role enforcement and tenant mine scoping.
Must NOT modify database records or bypass relationship query optimization.
"""
import datetime
import io
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.reading import Reading
from app.models.user import User
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.roles import SUPERIOR_ROLES
from app.services import clock
from app.services.hazard_classifier import (
    LEVEL_INT_TO_STR,
    get_hazard_details,
)
from app.services.packet_codec import norm_mac

router = APIRouter(tags=["export"])

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


@router.get("/api/export.xlsx")
def export_xlsx(
    gateway: Optional[str] = None,
    node_id: Optional[int] = None,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Excel workbook export with eager relationship loading (superiors only, mine scoped)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    allowed_mines = get_allowed_mine_ids(db, user)

    q = (
        db.query(Reading)
        .options(joinedload(Reading.node).joinedload(Node.gateway))
        .join(Node)
        .join(Gateway)
        .filter(Gateway.mine_id.in_(allowed_mines))
    )
    if gateway:
        clean_mac = norm_mac(gateway)
        gw_record = db.query(Gateway).filter(Gateway.mac == clean_mac).first()
        if not gw_record or gw_record.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Gateway not found")
        q = q.filter(Gateway.mac == clean_mac)

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
        ana = get_hazard_details(
            tilt_x=r.tilt_x,
            tilt_y=r.tilt_y,
            vib_rms=r.vib_rms,
            shock=r.shock,
            batt_v=r.batt_v,
            batt_pct=r.batt_pct,
        )
        gw_name = r.node.gateway.name if (r.node and r.node.gateway) else "Gateway"
        gw_mac = r.node.gateway.mac if (r.node and r.node.gateway) else "UNKNOWN"
        node_num = r.node.node_no if r.node else 0

        line = [
            r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            round(r.ts.timestamp(), 3),
            gw_name,
            gw_mac,
            node_num,
            r.counter,
            round(float(r.tilt_x), 3),
            round(float(r.tilt_y), 3),
            round(float(r.vib_rms), 4),
            "YES (Triggered)" if r.shock else "NO",
            round(float(r.batt_v), 3),
            round(float(r.batt_pct), 1),
            st.upper(),
            round(ana["score"], 3),
            "; ".join(ana["reasons"]),
        ]
        ws_all.append(line)

        sheet_key = (gw_mac, node_num)
        ws_node = sheets_by_node.get(sheet_key)
        if ws_node is None:
            tail = gw_mac.replace(":", "")[-6:]
            tab_name = f"GW {tail} Node {node_num}"[:31]
            ws_node = sheets_by_node[sheet_key] = wb.create_sheet(title=tab_name)
            style_headers(ws_node)
        ws_node.append(line)

    for sheet in wb.worksheets:
        sheet.auto_filter.ref = sheet.dimensions

    buf = io.BytesIO()
    wb.save(buf)
    filename = f"mine_subsidence_telemetry_{clock.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
