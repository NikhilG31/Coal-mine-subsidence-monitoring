"""Worker safety reports and device SOS API routes.

Owns HTTP request handling for report creation, supervisor status transitions, and role-scoped report lists.
Must NOT execute raw database mutations directly or evaluate offline thresholds.
"""
import datetime
from typing import List, Optional, Union
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.worker_report import WorkerReport
from app.schemas.reports import (
    CreateReportRequest,
    ReportMineInfo,
    ReportNodeInfo,
    ReportTunnelInfo,
    ReportUserInfo,
    SuperiorReportView,
    UpdateReportRequest,
    WorkerReportView,
)
from app.security.dependencies import get_current_user, require_role
from app.security.roles import SUPERIOR_ROLES
from app.services import clock
from app.services.report_service import (
    ReportConflictError,
    ReportError,
    ReportRateLimitError,
    acknowledge_report,
    create_worker_report,
    list_reports,
    resolve_report,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _serialize_superior_report(r: WorkerReport) -> SuperiorReportView:
    emp_no = None
    reporter_info = None
    if r.reporter:
        if r.reporter.worker_profile:
            emp_no = r.reporter.worker_profile.employee_no
        reporter_info = ReportUserInfo(
            id=r.reporter.id,
            full_name=r.reporter.full_name,
            employee_no=emp_no,
        )

    node_info = None
    if r.node:
        gw_name = r.node.gateway.name if r.node.gateway else None
        node_info = ReportNodeInfo(
            id=r.node.id,
            node_no=r.node.node_no,
            gateway_name=gw_name,
        )

    return SuperiorReportView(
        id=r.id,
        source=r.source,
        type=r.type,
        severity=r.severity,
        description=r.description,
        status=r.status,
        created_at=r.created_at.astimezone(datetime.timezone.utc).isoformat(),
        last_signal_at=r.last_signal_at.astimezone(datetime.timezone.utc).isoformat(),
        repeat_count=r.repeat_count,
        mine=ReportMineInfo(id=r.mine.id, name=r.mine.name),
        tunnel=ReportTunnelInfo(id=r.tunnel.id, name=r.tunnel.name) if r.tunnel else None,
        reporter=reporter_info,
        node=node_info,
        acknowledged_by_name=r.acknowledger.full_name if r.acknowledger else None,
        acknowledged_at=r.acknowledged_at.astimezone(datetime.timezone.utc).isoformat() if r.acknowledged_at else None,
        resolved_by_name=r.resolver.full_name if r.resolver else None,
        resolved_at=r.resolved_at.astimezone(datetime.timezone.utc).isoformat() if r.resolved_at else None,
        resolution_note=r.resolution_note,
        incident_id=r.incident_id,
    )


def _serialize_worker_report(r: WorkerReport) -> WorkerReportView:
    return WorkerReportView(
        id=r.id,
        type=r.type,
        severity=r.severity,
        description=r.description,
        status=r.status,
        created_at=r.created_at.astimezone(datetime.timezone.utc).isoformat(),
        acknowledged_at=r.acknowledged_at.astimezone(datetime.timezone.utc).isoformat() if r.acknowledged_at else None,
        resolved_at=r.resolved_at.astimezone(datetime.timezone.utc).isoformat() if r.resolved_at else None,
        tunnel_name=r.tunnel.name if r.tunnel else None,
    )


@router.post("", response_model=WorkerReportView, status_code=status.HTTP_201_CREATED)
def post_report(
    req: CreateReportRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Any logged-in user can raise a worker report. Returns worker-safe view."""
    now = clock.utcnow()
    try:
        report = create_worker_report(
            db=db,
            user=current_user,
            type=req.type,
            severity=req.severity,
            tunnel_id=req.tunnel_id,
            description=req.description,
            now=now,
        )
        db.commit()
        db.refresh(report)
    except ReportRateLimitError as exc:
        db.rollback()
        return JSONResponse(
            status_code=429,
            content={"code": exc.code, "detail": exc.detail},
            headers={"Retry-After": str(exc.retry_after)},
        )
    except ReportConflictError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={"code": exc.code, "detail": exc.detail},
        )
    except ReportError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return _serialize_worker_report(report)


@router.get("", response_model=Union[List[SuperiorReportView], List[WorkerReportView]])
def get_reports(
    status_filter: Optional[str] = Query(None, alias="status", pattern="^(open|acknowledged|resolved)$"),
    type_filter: Optional[str] = Query(None, alias="type", pattern="^(injury|equipment_fault|gas_smell|need_help|sos)$"),
    mine_id: Optional[int] = Query(None),
    tunnel_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lists reports scoped by user role. Superiors get full view; workers get own worker-safe view."""
    now = clock.utcnow()
    try:
        reports = list_reports(
            db=db,
            user=current_user,
            status=status_filter,
            type=type_filter,
            mine_id=mine_id,
            tunnel_id=tunnel_id,
            limit=limit,
            offset=offset,
            now=now,
        )
    except ReportError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    if current_user.role in SUPERIOR_ROLES:
        return [_serialize_superior_report(r) for r in reports]
    return [_serialize_worker_report(r) for r in reports]


@router.patch("/{id}", response_model=SuperiorReportView)
def patch_report(
    id: int,
    req: UpdateReportRequest,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: advances report status to acknowledged or resolved."""
    now = clock.utcnow()
    try:
        if req.status == "acknowledged":
            report = acknowledge_report(db, actor=current_user, report_id=id, now=now)
        elif req.status == "resolved":
            report = resolve_report(
                db,
                actor=current_user,
                report_id=id,
                resolution_note=req.resolution_note,
                now=now,
            )
        else:
            raise ReportConflictError("Invalid status transition: cannot transition back to open")
        db.commit()
        db.refresh(report)
    except ReportConflictError as exc:
        db.rollback()
        return JSONResponse(
            status_code=409,
            content={"code": exc.code, "detail": exc.detail},
        )
    except ReportError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    return _serialize_superior_report(report)
