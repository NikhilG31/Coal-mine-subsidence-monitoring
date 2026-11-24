"""HTTP router for gate check-in/out scans, scan history, and live tunnel headcount.

Owns POST /api/checkins, POST /api/checkins/manual, GET /api/checkins, and GET /api/headcount.
Must NOT define database models, calculate raw sensor physics, or store unhashed badge tokens.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.check_event import CheckEvent
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.checkins import (
    CheckEventsListResponse,
    HeadcountResponse,
    ManualScanRequest,
    ScanRequest,
    ScanResponse,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.rate_limit import (
    check_scan_rate_limit,
    check_unknown_badge_rate_limit,
    record_scan_attempt,
    record_unknown_badge_failure,
)
from app.security.roles import (
    ROLE_ENGINEER,
    ROLE_FOREMAN,
    ROLE_MANAGER,
    ROLE_SAFETY_OFFICER,
)
from app.services.checkin_service import (
    CheckInError,
    UnknownBadge,
    record_manual,
    record_scan,
)
from app.services import clock
from app.services.headcount import headcount_by_tunnel
from app.services.tunnel_scope import get_current_tunnel_id

router = APIRouter(tags=["checkins"])

SCANNER_ROLES = (ROLE_MANAGER, ROLE_SAFETY_OFFICER, ROLE_ENGINEER, ROLE_FOREMAN)


@router.post("/api/checkins", response_model=ScanResponse)
def api_record_scan(
    req: ScanRequest,
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Records a QR badge scan event at the tunnel portal."""
    # 1. Rate limit checks
    retry_after = check_scan_rate_limit(user.id)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Scan rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    retry_after_unknown = check_unknown_badge_rate_limit(user.id)
    if retry_after_unknown is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many unknown badge scan attempts",
            headers={"Retry-After": str(retry_after_unknown)},
        )

    record_scan_attempt(user.id)

    # 2. Process scan
    try:
        result = record_scan(db, user, req.token, req.direction, tunnel_id=req.tunnel_id)
        return result
    except UnknownBadge as exc:
        record_unknown_badge_failure(user.id)
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        if exc.extra:
            content.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=content)
    except CheckInError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        if exc.extra:
            content.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=content)


@router.post("/api/checkins/manual", response_model=ScanResponse)
def api_record_manual(
    req: ManualScanRequest,
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Manually records a check-in or check-out event with an audit entry."""
    retry_after = check_scan_rate_limit(user.id)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Scan rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )

    record_scan_attempt(user.id)

    try:
        result = record_manual(
            db,
            user,
            user_id=req.user_id,
            direction=req.direction,
            tunnel_id=req.tunnel_id,
        )
        return result
    except CheckInError as exc:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        if exc.extra:
            content.update(exc.extra)
        return JSONResponse(status_code=exc.status_code, content=content)


@router.get("/api/checkins", response_model=CheckEventsListResponse)
def api_list_checkins(
    tunnel_id: Optional[int] = None,
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Lists recent check-in and check-out events scoped to permitted tunnels."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, user)

    if user.role == ROLE_FOREMAN:
        curr_tid = get_current_tunnel_id(db, user.id, now)
        if not curr_tid:
            return {"events": []}
        if tunnel_id is not None and tunnel_id != curr_tid:
            raise HTTPException(status_code=403, detail="Foreman may only view own tunnel")
        target_tunnel_ids = [curr_tid]
    else:
        if tunnel_id is not None:
            tunnel = db.query(Tunnel).filter(Tunnel.id == tunnel_id).first()
            if not tunnel or tunnel.mine_id not in allowed_mines:
                raise HTTPException(status_code=404, detail="Tunnel not found")
            target_tunnel_ids = [tunnel_id]
        else:
            tunnels = db.query(Tunnel).filter(Tunnel.mine_id.in_(allowed_mines)).all()
            target_tunnel_ids = [t.id for t in tunnels]

    if not target_tunnel_ids:
        return {"events": []}

    events = (
        db.query(CheckEvent)
        .options(
            joinedload(CheckEvent.user).joinedload(User.worker_profile),
            joinedload(CheckEvent.tunnel),
            joinedload(CheckEvent.scanner),
        )
        .filter(
            CheckEvent.tunnel_id.in_(target_tunnel_ids),
            CheckEvent.user.has(User.company_id == user.company_id),
        )
        .order_by(CheckEvent.scanned_at.desc(), CheckEvent.id.desc())
        .limit(limit)
        .all()
    )

    items = []
    for ev in events:
        w = ev.user
        emp_no = w.worker_profile.employee_no if w and w.worker_profile else None
        scanner_dict = {"id": ev.scanner.id, "full_name": ev.scanner.full_name} if ev.scanner else None
        items.append(
            {
                "id": ev.id,
                "worker": {
                    "id": w.id if w else ev.user_id,
                    "full_name": w.full_name if w else "Unknown",
                    "employee_no": emp_no,
                },
                "tunnel": {
                    "id": ev.tunnel.id if ev.tunnel else ev.tunnel_id,
                    "name": ev.tunnel.name if ev.tunnel else f"Tunnel {ev.tunnel_id}",
                },
                "direction": ev.direction,
                "method": ev.method,
                "scanned_at": ev.scanned_at.astimezone(datetime.timezone.utc).isoformat(),
                "scanner": scanner_dict,
            }
        )

    return {"events": items}


@router.get("/api/headcount", response_model=HeadcountResponse)
def api_get_headcount(
    mine_id: Optional[int] = None,
    user: User = Depends(require_role(*SCANNER_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns real-time underground tunnel headcount and active scheduled worker counts."""
    now = clock.utcnow()
    allowed_mines = get_allowed_mine_ids(db, user)

    if user.role == ROLE_FOREMAN:
        curr_tid = get_current_tunnel_id(db, user.id, now)
        if not curr_tid:
            return {"tunnels": []}
        target_tunnel_ids = [curr_tid]
    else:
        if mine_id is not None:
            if mine_id not in allowed_mines:
                raise HTTPException(status_code=404, detail="Mine not found")
            target_mines = [mine_id]
        else:
            target_mines = allowed_mines
        tunnels = db.query(Tunnel).filter(Tunnel.mine_id.in_(target_mines)).all()
        target_tunnel_ids = [t.id for t in tunnels]

    headcount_map = headcount_by_tunnel(db, target_tunnel_ids, now=now)
    return {"tunnels": list(headcount_map.values())}
