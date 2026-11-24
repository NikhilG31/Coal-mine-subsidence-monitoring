"""HTTP router for emergency incidents, siren controls, and worker roll call.

Owns REST endpoints for incident creation, status lifecycle, roll call roster, and accounting.
Must NOT execute raw telemetry mutations or define database table schemas.
"""
import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.incident import Incident
from app.models.incident_roster import IncidentRoster
from app.models.mine import Mine
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.incidents import (
    IncidentCreateRequest,
    IncidentDetailResponse,
    IncidentRosterItem,
    IncidentUpdateRequest,
    IncidentsListResponse,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.roles import ROLE_FOREMAN, SUPERIOR_ROLES
from app.services import clock
from app.services.incident_service import (
    IncidentConflictError,
    IncidentError,
    close_incident,
    mark_accounted,
    open_incident,
    set_siren,
)
from app.services.tunnel_scope import get_current_tunnel_id

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


def _serialize_incident(db: Session, inc: Incident, counts_map: Optional[Dict[int, tuple]] = None) -> Dict[str, Any]:
    if counts_map is not None and inc.id in counts_map:
        total_count, accounted_count = counts_map[inc.id]
    else:
        counts = (
            db.query(
                func.count(IncidentRoster.user_id),
                func.count(IncidentRoster.accounted_at),
            )
            .filter(IncidentRoster.incident_id == inc.id)
            .first()
        )
        total_count = counts[0] if counts else 0
        accounted_count = counts[1] if counts else 0
    unaccounted_count = total_count - accounted_count

    return {
        "id": inc.id,
        "mine_id": inc.mine_id,
        "mine_name": inc.mine.name if inc.mine else None,
        "tunnel_id": inc.tunnel_id,
        "tunnel_name": inc.tunnel.name if inc.tunnel else None,
        "source": inc.source,
        "level": inc.level,
        "title": inc.title,
        "status": inc.status,
        "siren_active": inc.siren_active,
        "opened_by": inc.opened_by,
        "opened_by_name": inc.opener.full_name if inc.opener else None,
        "opened_at": inc.opened_at.astimezone(datetime.timezone.utc).isoformat(),
        "closed_at": inc.closed_at.astimezone(datetime.timezone.utc).isoformat() if inc.closed_at else None,
        "closed_by": inc.closed_by,
        "closed_by_name": inc.closer.full_name if inc.closer else None,
        "close_note": inc.close_note,
        "unaccounted_count": unaccounted_count,
        "accounted_count": accounted_count,
        "total_count": total_count,
    }


def _verify_incident_scope(incident: Incident, user: User, db: Session, now: datetime.datetime) -> None:
    """Verifies company and mine access, and checks foreman own-tunnel restriction."""
    if not incident.mine or incident.mine.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Incident not found")

    allowed_mines = get_allowed_mine_ids(db, user)
    if allowed_mines is not None and incident.mine_id not in allowed_mines:
        raise HTTPException(status_code=404, detail="Incident not found")

    if user.role == ROLE_FOREMAN:
        current_tunnel_id = get_current_tunnel_id(db, user.id, now)
        if not current_tunnel_id or incident.tunnel_id != current_tunnel_id:
            raise HTTPException(status_code=404, detail="Incident not found")


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentDetailResponse)
def create_incident(
    req: IncidentCreateRequest,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: opens a new emergency or evacuation incident."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, current_user)
    if allowed_mines is not None and req.mine_id not in allowed_mines:
        raise HTTPException(status_code=404, detail="Mine not found")

    mine = (
        db.query(Mine)
        .filter(Mine.id == req.mine_id, Mine.company_id == current_user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")

    if req.tunnel_id is not None:
        tunnel = (
            db.query(Tunnel)
            .filter(Tunnel.id == req.tunnel_id, Tunnel.mine_id == req.mine_id)
            .first()
        )
        if not tunnel:
            raise HTTPException(status_code=404, detail="Tunnel not found in specified mine")

    try:
        inc = open_incident(
            db=db,
            actor=current_user,
            mine_id=req.mine_id,
            tunnel_id=req.tunnel_id,
            title=req.title,
            level=req.level,
            siren=req.siren,
            source="manual",
            now=now,
        )
        db.commit()
        db.refresh(inc)
    except IncidentError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return _serialize_incident(db, inc)


@router.get("", response_model=IncidentsListResponse)
def list_incidents(
    status_filter: str = Query("open", alias="status", pattern="^(open|all)$"),
    mine_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_role(*SUPERIOR_ROLES, ROLE_FOREMAN)),
    db: Session = Depends(get_db),
):
    """Lists incidents filtered by status and mine, scoped by role (with limit and offset)."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, current_user)

    query = (
        db.query(Incident)
        .join(Mine, Incident.mine_id == Mine.id)
        .options(
            joinedload(Incident.mine),
            joinedload(Incident.tunnel),
            joinedload(Incident.opener),
            joinedload(Incident.closer),
        )
        .filter(Mine.company_id == current_user.company_id)
    )

    if allowed_mines is not None:
        query = query.filter(Incident.mine_id.in_(allowed_mines))

    if mine_id is not None:
        if allowed_mines is not None and mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Mine not found")
        query = query.filter(Incident.mine_id == mine_id)

    if status_filter == "open":
        query = query.filter(Incident.status == "open")

    if current_user.role == ROLE_FOREMAN:
        current_tunnel_id = get_current_tunnel_id(db, current_user.id, now)
        if not current_tunnel_id:
            return IncidentsListResponse(incidents=[])
        query = query.filter(Incident.tunnel_id == current_tunnel_id)

    incidents = query.order_by(Incident.opened_at.desc()).offset(offset).limit(limit).all()
    if not incidents:
        return IncidentsListResponse(incidents=[])

    # Bulk query roll call counts to avoid N+1 queries
    inc_ids = [inc.id for inc in incidents]
    counts_rows = (
        db.query(
            IncidentRoster.incident_id,
            func.count(IncidentRoster.user_id),
            func.count(IncidentRoster.accounted_at),
        )
        .filter(IncidentRoster.incident_id.in_(inc_ids))
        .group_by(IncidentRoster.incident_id)
        .all()
    )
    counts_map = {row[0]: (row[1], row[2]) for row in counts_rows}

    items = [_serialize_incident(db, inc, counts_map) for inc in incidents]
    return IncidentsListResponse(incidents=items)


@router.get("/{id}", response_model=IncidentDetailResponse)
def get_incident(
    id: int,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES, ROLE_FOREMAN)),
    db: Session = Depends(get_db),
):
    """Retrieves an incident by ID with live roll call metrics."""
    now = clock.utcnow()
    inc = (
        db.query(Incident)
        .options(
            joinedload(Incident.mine),
            joinedload(Incident.tunnel),
            joinedload(Incident.opener),
            joinedload(Incident.closer),
        )
        .filter(Incident.id == id)
        .first()
    )
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    _verify_incident_scope(inc, current_user, db, now)
    return _serialize_incident(db, inc)


@router.get("/{id}/roster", response_model=List[IncidentRosterItem])
def get_incident_roster(
    id: int,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES, ROLE_FOREMAN)),
    db: Session = Depends(get_db),
):
    """Lists the snapshot roll call personnel for an incident."""
    now = clock.utcnow()
    inc = db.query(Incident).options(joinedload(Incident.mine)).filter(Incident.id == id).first()
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    _verify_incident_scope(inc, current_user, db, now)

    roster_rows = (
        db.query(IncidentRoster)
        .options(
            joinedload(IncidentRoster.user).joinedload(User.worker_profile),
            joinedload(IncidentRoster.accounted_user),
        )
        .filter(IncidentRoster.incident_id == id)
        .order_by(
            IncidentRoster.accounted_at.isnot(None),  # Unaccounted (None) first
            IncidentRoster.added_at.asc(),
        )
        .all()
    )

    items = []
    for r in roster_rows:
        emp_no = r.user.worker_profile.employee_no if r.user and r.user.worker_profile else None
        items.append(
            IncidentRosterItem(
                user_id=r.user_id,
                full_name=r.user.full_name if r.user else "Unknown",
                employee_no=emp_no,
                added_at=r.added_at.astimezone(datetime.timezone.utc).isoformat(),
                accounted_at=r.accounted_at.astimezone(datetime.timezone.utc).isoformat() if r.accounted_at else None,
                accounted_by_name=r.accounted_user.full_name if r.accounted_user else None,
                late_arrival=bool(r.added_at > inc.opened_at),
            )
        )

    return items


@router.post("/{id}/roster/{user_id}/account")
def account_worker(
    id: int,
    user_id: int,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES, ROLE_FOREMAN)),
    db: Session = Depends(get_db),
):
    """Marks a worker accounted for in the incident roll call (locks incident FOR UPDATE)."""
    now = clock.utcnow()
    inc = (
        db.query(Incident)
        .filter(Incident.id == id)
        .with_for_update(of=Incident)
        .first()
    )
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    _verify_incident_scope(inc, current_user, db, now)

    try:
        entry = mark_accounted(db, actor=current_user, incident=inc, user_id=user_id, now=now)
        db.commit()
    except IncidentError as exc:
        db.rollback()
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)

    return {
        "ok": True,
        "incident_id": id,
        "user_id": user_id,
        "accounted_at": entry.accounted_at.astimezone(datetime.timezone.utc).isoformat() if entry.accounted_at else None,
    }


@router.patch("/{id}", response_model=IncidentDetailResponse)
def update_incident(
    id: int,
    req: IncidentUpdateRequest,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: closes an incident or modifies siren state (locks incident FOR UPDATE)."""
    now = clock.utcnow()
    inc = (
        db.query(Incident)
        .filter(Incident.id == id)
        .with_for_update(of=Incident)
        .first()
    )
    if not inc:
        raise HTTPException(status_code=404, detail="Incident not found")

    _verify_incident_scope(inc, current_user, db, now)

    try:
        if req.siren is not None:
            set_siren(db, actor=current_user, incident=inc, on=req.siren, now=now)

        if req.close is True:
            close_incident(
                db=db,
                actor=current_user,
                incident=inc,
                close_note=req.close_note,
                confirm_unaccounted=req.confirm_unaccounted or False,
                now=now,
            )

        db.commit()
        db.refresh(inc)
    except IncidentConflictError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={"code": exc.code, "detail": exc.detail, "count": exc.count},
        )
    except IncidentError as exc:
        db.rollback()
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content)

    return _serialize_incident(db, inc)
