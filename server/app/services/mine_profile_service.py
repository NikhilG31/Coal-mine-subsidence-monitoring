"""Mine profile assembly, safe viewing, and metadata mutation service.

Owns five-group profile construction, role-based worker model projection, and PATCH updates.
Must NOT handle HTTP requests, import FastAPI components, or leak sensitive telemetry to workers.
"""
import datetime
from typing import Any, Dict, List, Optional
import zoneinfo
from sqlalchemy.orm import Session

from app.config import HOURS_TIMEZONE
from app.models.mine import Mine
from app.models.mine_contact import MineContact
from app.models.tunnel import Tunnel
from app.models.user import User
from app.services.audit import record_audit
from app.services import clock
from app.services.mine_headcount_service import headcount_by_tunnel_and_shift
from app.services.production_service import month_summary
from app.services.tunnel_scope import get_current_tunnel_id

_VALID_STATUSES = {"active", "maintenance", "suspended", "closed"}
_VALID_METHODS = {"opencast", "underground", "mixed"}


class DuplicateMineNameError(Exception):
    """Raised when updating a mine name clashes with an existing mine in the company."""
    pass


def build_superior_profile(
    db: Session,
    user: User,
    mine: Mine,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Assembles all FIVE profile groups for superiors (B0).

    1. identity_status: {id, name, status, lat, lon, evacuation_notes}
    2. company_classification: {company_name, subsidiary, method, area_ha, max_depth_m}
    3. headcount: {totals: {inside, scheduled_now, upcoming}, tunnels: [...]}
    4. production: current month summary in HOURS_TIMEZONE
    5. contacts: [{id, role, name, phone, rank}] ordered by rank
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # 1. Identity & Status
    identity_status = {
        "id": mine.id,
        "name": mine.name,
        "status": mine.status,
        "lat": mine.lat,
        "lon": mine.lon,
        "evacuation_notes": mine.evacuation_notes,
    }

    # 2. Company & Classification
    company_name = mine.company.name if mine.company else ""
    company_classification = {
        "company_name": company_name,
        "subsidiary": mine.subsidiary,
        "method": mine.method,
        "area_ha": mine.area_ha,
        "max_depth_m": mine.max_depth_m,
    }

    # 3. Headcount by tunnel & shift lookahead
    headcount = headcount_by_tunnel_and_shift(db, mine, now)

    # 4. Production summary for current month in HOURS_TIMEZONE
    tz = zoneinfo.ZoneInfo(HOURS_TIMEZONE)
    cur_month = now.astimezone(tz).strftime("%Y-%m")
    production = month_summary(db, mine.id, cur_month)

    # 5. Key Contacts ordered by rank
    contacts_rows = (
        db.query(MineContact)
        .filter(MineContact.mine_id == mine.id)
        .order_by(MineContact.rank.asc())
        .all()
    )
    contacts = [
        {
            "id": c.id,
            "role": c.role,
            "name": c.name,
            "phone": c.phone,
            "rank": c.rank,
        }
        for c in contacts_rows
    ]

    return {
        "identity_status": identity_status,
        "company_classification": company_classification,
        "headcount": headcount,
        "production": production,
        "contacts": contacts,
    }


def build_worker_profile(
    db: Session,
    user: User,
    mine: Mine,
    now: Optional[datetime.datetime] = None,
) -> Dict[str, Any]:
    """Assembles the trimmed WORKER-SAFE model (B4).

    Contains: {id, name, status, evacuation_notes, contacts: [{role, name, phone, rank}],
               current_tunnel: {id, name, status} or null}
    STRICTLY OMITS: production, headcount, company, area, depth, lat, lon, IDs of other people.
    """
    if now is None:
        now = clock.utcnow()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)

    # Contacts ordered by rank (WITHOUT database IDs or user IDs)
    contacts_rows = (
        db.query(MineContact)
        .filter(MineContact.mine_id == mine.id)
        .order_by(MineContact.rank.asc())
        .all()
    )
    contacts = [
        {
            "role": c.role,
            "name": c.name,
            "phone": c.phone,
            "rank": c.rank,
        }
        for c in contacts_rows
    ]

    # Current tunnel via services/tunnel_scope
    current_tunnel_id = get_current_tunnel_id(db, user.id, now)
    current_tunnel = None
    if current_tunnel_id is not None:
        tun = db.query(Tunnel).filter(Tunnel.id == current_tunnel_id).first()
        if tun and tun.mine_id == mine.id:
            current_tunnel = {
                "id": tun.id,
                "name": tun.name,
                "status": tun.status,
            }

    return {
        "id": mine.id,
        "name": mine.name,
        "status": mine.status,
        "evacuation_notes": mine.evacuation_notes,
        "contacts": contacts,
        "current_tunnel": current_tunnel,
    }


def update_profile(
    db: Session,
    actor: Any,
    mine: Mine,
    fields: Dict[str, Any],
) -> Mine:
    """Updates mine metadata with PATCH semantics (B3).

    Validates:
    - Enums (status, method)
    - lat and lon both or neither, -90..90 / -180..180
    - name 1-255 trimmed, duplicate name in company -> DuplicateMineNameError (409)
    - area_ha and max_depth_m >= 0
    - evacuation_notes <= 2000 chars, control characters stripped
    - Audits mine.update with detail = NAMES of changed fields only (never notes, never values)
    """
    changed_fields = set()

    # 1. Status
    if "status" in fields and fields["status"] is not None:
        val = fields["status"]
        if val not in _VALID_STATUSES:
            raise ValueError(f"Invalid mine status '{val}'. Must be one of: {sorted(list(_VALID_STATUSES))}")
        if mine.status != val:
            mine.status = val
            changed_fields.add("status")

    # 2. Method
    if "method" in fields and fields["method"] is not None:
        val = fields["method"]
        if val not in _VALID_METHODS:
            raise ValueError(f"Invalid mining method '{val}'. Must be one of: {sorted(list(_VALID_METHODS))}")
        if mine.method != val:
            mine.method = val
            changed_fields.add("method")

    # 3. Name
    if "name" in fields and fields["name"] is not None:
        name_val = fields["name"]
        if not isinstance(name_val, str) or not name_val.strip():
            raise ValueError("Mine name must be a non-empty string between 1 and 255 characters")
        name_val = name_val.strip()
        if len(name_val) > 255:
            raise ValueError("Mine name cannot exceed 255 characters")

        if mine.name != name_val:
            # Check for duplicate in same company
            existing = (
                db.query(Mine)
                .filter(
                    Mine.company_id == mine.company_id,
                    Mine.name == name_val,
                    Mine.id != mine.id,
                )
                .first()
            )
            if existing:
                raise DuplicateMineNameError(f"Mine with name '{name_val}' already exists in your company")
            mine.name = name_val
            changed_fields.add("name")

    # 4. Subsidiary
    if "subsidiary" in fields:
        sub_val = fields["subsidiary"]
        if sub_val is not None:
            if not isinstance(sub_val, str):
                raise ValueError("subsidiary must be a string or null")
            sub_val = sub_val.strip() or None
        if mine.subsidiary != sub_val:
            mine.subsidiary = sub_val
            changed_fields.add("subsidiary")

    # 5. Lat and Lon (both or neither)
    has_lat = "lat" in fields
    has_lon = "lon" in fields
    if has_lat or has_lon:
        if not (has_lat and has_lon):
            raise ValueError("lat and lon must both be provided together or omitted")

        new_lat = fields["lat"]
        new_lon = fields["lon"]

        if (new_lat is None) != (new_lon is None):
            raise ValueError("lat and lon must both be set or both be null")

        if new_lat is not None:
            try:
                lat_f = float(new_lat)
                lon_f = float(new_lon)
            except (ValueError, TypeError):
                raise ValueError("lat and lon must be numeric coordinates")

            if not (-90.0 <= lat_f <= 90.0):
                raise ValueError("lat must be between -90 and 90 degrees")
            if not (-180.0 <= lon_f <= 180.0):
                raise ValueError("lon must be between -180 and 180 degrees")

            if mine.lat != lat_f:
                mine.lat = lat_f
                changed_fields.add("lat")
            if mine.lon != lon_f:
                mine.lon = lon_f
                changed_fields.add("lon")
        else:
            if mine.lat is not None:
                mine.lat = None
                changed_fields.add("lat")
            if mine.lon is not None:
                mine.lon = None
                changed_fields.add("lon")

    # 6. area_ha
    if "area_ha" in fields:
        area_val = fields["area_ha"]
        if area_val is not None:
            try:
                area_f = float(area_val)
            except (ValueError, TypeError):
                raise ValueError("area_ha must be a numeric value")
            if area_f < 0:
                raise ValueError("area_ha must be non-negative")
            if mine.area_ha != area_f:
                mine.area_ha = area_f
                changed_fields.add("area_ha")
        else:
            if mine.area_ha is not None:
                mine.area_ha = None
                changed_fields.add("area_ha")

    # 7. max_depth_m
    if "max_depth_m" in fields:
        depth_val = fields["max_depth_m"]
        if depth_val is not None:
            try:
                depth_f = float(depth_val)
            except (ValueError, TypeError):
                raise ValueError("max_depth_m must be a numeric value")
            if depth_f < 0:
                raise ValueError("max_depth_m must be non-negative")
            if mine.max_depth_m != depth_f:
                mine.max_depth_m = depth_f
                changed_fields.add("max_depth_m")
        else:
            if mine.max_depth_m is not None:
                mine.max_depth_m = None
                changed_fields.add("max_depth_m")

    # 8. evacuation_notes (<= 2000 chars, control characters stripped)
    if "evacuation_notes" in fields:
        notes_val = fields["evacuation_notes"]
        if notes_val is not None:
            if not isinstance(notes_val, str):
                raise ValueError("evacuation_notes must be a string or null")
            # Strip control characters (preserve newline, carriage return, and tab)
            cleaned = "".join(
                ch for ch in notes_val
                if ord(ch) >= 32 or ch in ("\n", "\r", "\t")
            )
            if len(cleaned) > 2000:
                raise ValueError("evacuation_notes cannot exceed 2000 characters")
            notes_val = cleaned or None
        if mine.evacuation_notes != notes_val:
            mine.evacuation_notes = notes_val
            changed_fields.add("evacuation_notes")

    if changed_fields:
        db.flush()
        record_audit(
            db=db,
            user=actor,
            action="mine.update",
            entity="mine",
            entity_id=mine.id,
            detail={"fields": sorted(list(changed_fields))},
            company_id=mine.company_id,
        )
        db.commit()
        db.refresh(mine)

    return mine
