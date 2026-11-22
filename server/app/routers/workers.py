"""HTTP router for mine worker discovery, inspection, and profile management.

Owns GET /api/workers, GET /api/workers/{id}, and PUT /api/workers/{id}/profile endpoints.
Must NOT perform low-level shift arithmetic, bypass mine scoping, or leak phone numbers into audit logs.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.worker_profile import WorkerProfile
from app.schemas.checkins import BadgeIssueResponse, BadgeRevokeResponse
from app.schemas.workers import (
    ProfileUpsertRequest,
    WorkerDetailResponse,
    WorkersListResponse,
)
from app.security.dependencies import require_role, security_logger
from app.security.roles import ROLE_FOREMAN, ROLE_MANAGER, ROLE_MINER, SUPERIOR_ROLES
from app.services.audit import record_audit
from app.services.badge_service import BadgeError, issue_badge, revoke_badge
from app.services.tunnel_scope import get_current_tunnel_id
from app.services.worker_queries import (
    WorkerQueryError,
    get_worker_detail,
    list_workers,
)

router = APIRouter(tags=["workers"])


@router.get("/api/workers", response_model=WorkersListResponse)
def api_list_workers(
    mine_id: Optional[int] = None,
    tunnel_id: Optional[int] = None,
    q: Optional[str] = None,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Lists mine workers (foremen and miners) within caller's authorized scope."""
    try:
        workers = list_workers(db, user, mine_id=mine_id, tunnel_id=tunnel_id, q=q)
        return {"workers": workers}
    except WorkerQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/api/workers/{worker_id}", response_model=WorkerDetailResponse)
def api_get_worker(
    worker_id: int,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns detailed worker profile and shift history within caller's authorized scope."""
    try:
        detail = get_worker_detail(db, user, worker_id)
        return detail
    except WorkerQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.put("/api/workers/{worker_id}/profile")
def api_update_worker_profile(
    worker_id: int,
    req: ProfileUpsertRequest,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Creates or updates a worker's profile (Manager only; audited without contact leaks)."""
    target_user = (
        db.query(User)
        .filter(
            User.id == worker_id,
            User.company_id == user.company_id,
        )
        .first()
    )
    if not target_user:
        raise HTTPException(status_code=404, detail="Worker not found")
    if target_user.role not in (ROLE_FOREMAN, ROLE_MINER):
        raise HTTPException(
            status_code=400,
            detail="Worker profile is only allowed for foreman or miner roles",
        )

    # Pre-check duplicate employee_no in the same company
    dup_profile = (
        db.query(WorkerProfile)
        .filter(
            WorkerProfile.company_id == user.company_id,
            WorkerProfile.employee_no == req.employee_no,
            WorkerProfile.user_id != worker_id,
        )
        .first()
    )
    if dup_profile:
        raise HTTPException(status_code=409, detail="Employee number already in use")

    profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == worker_id).first()

    changed_fields = []
    if profile is None:
        changed_fields = ["employee_no", "phone", "emergency_contact_name", "emergency_contact_phone", "joined_on"]
        profile = WorkerProfile(
            user_id=target_user.id,
            company_id=target_user.company_id,
            employee_no=req.employee_no,
            phone=req.phone,
            emergency_contact_name=req.emergency_contact_name,
            emergency_contact_phone=req.emergency_contact_phone,
            joined_on=req.joined_on,
        )
        db.add(profile)
    else:
        if profile.employee_no != req.employee_no:
            changed_fields.append("employee_no")
        if profile.phone != req.phone:
            changed_fields.append("phone")
        if profile.emergency_contact_name != req.emergency_contact_name:
            changed_fields.append("emergency_contact_name")
        if profile.emergency_contact_phone != req.emergency_contact_phone:
            changed_fields.append("emergency_contact_phone")
        if profile.joined_on != req.joined_on:
            changed_fields.append("joined_on")

        profile.employee_no = req.employee_no
        profile.phone = req.phone
        profile.emergency_contact_name = req.emergency_contact_name
        profile.emergency_contact_phone = req.emergency_contact_phone
        profile.joined_on = req.joined_on

    # Audit logging: record ONLY changed field names, never actual phone/contact data
    record_audit(
        db,
        user,
        action="worker.profile_update",
        entity="worker",
        entity_id=target_user.id,
        detail={"changed_fields": changed_fields},
    )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        orig_msg = str(getattr(exc, "orig", exc))
        if "uq_worker_profiles_company_employee_no" in orig_msg:
            raise HTTPException(status_code=409, detail="Employee number already in use")
        raise

    db.refresh(profile)

    security_logger.info(
        "Manager '%s' updated profile for worker %d (%s)",
        user.username,
        target_user.id,
        req.employee_no,
    )
    return {
        "user_id": profile.user_id,
        "employee_no": profile.employee_no,
        "phone": profile.phone,
        "emergency_contact_name": profile.emergency_contact_name,
        "emergency_contact_phone": profile.emergency_contact_phone,
        "joined_on": profile.joined_on.isoformat() if profile.joined_on else None,
    }


@router.post("/api/workers/{worker_id}/badge", response_model=BadgeIssueResponse)
def api_issue_worker_badge(
    worker_id: int,
    response: Response,
    user: User = Depends(require_role(ROLE_MANAGER)),
    db: Session = Depends(get_db),
):
    """Issues a new cryptographic QR badge for an active foreman or miner (Manager only)."""
    try:
        plaintext_token, svg, qr_token = issue_badge(db, user, worker_id)
    except BadgeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)

    response.headers["Cache-Control"] = "no-store"
    worker = qr_token.user
    emp_no = worker.worker_profile.employee_no if worker.worker_profile else None
    return {
        "token": plaintext_token,
        "badge_svg": svg,
        "issued_at": qr_token.issued_at.astimezone(datetime.timezone.utc).isoformat(),
        "worker": {
            "id": worker.id,
            "full_name": worker.full_name,
            "employee_no": emp_no,
        },
    }


@router.delete("/api/workers/{worker_id}/badge", response_model=BadgeRevokeResponse)
def api_revoke_worker_badge(
    worker_id: int,
    user: User = Depends(require_role(ROLE_MANAGER, ROLE_FOREMAN)),
    db: Session = Depends(get_db),
):
    """Revokes active QR badges for a worker (Manager, or Foreman for their current tunnel)."""
    now = datetime.datetime.now(datetime.timezone.utc)
    if user.role == ROLE_FOREMAN:
        foreman_tid = get_current_tunnel_id(db, user.id, now)
        if not foreman_tid:
            raise HTTPException(status_code=404, detail="Worker not found")
        worker_tid = get_current_tunnel_id(db, worker_id, now)
        if worker_tid != foreman_tid:
            raise HTTPException(status_code=404, detail="Worker not found")
        target = db.query(User).filter(User.id == worker_id, User.company_id == user.company_id).first()
        if not target or target.role not in (ROLE_FOREMAN, ROLE_MINER):
            raise HTTPException(status_code=404, detail="Worker not found")
    else:
        target = db.query(User).filter(User.id == worker_id, User.company_id == user.company_id).first()
        if not target or target.role not in (ROLE_FOREMAN, ROLE_MINER):
            raise HTTPException(status_code=404, detail="Worker not found")

    revoked_count = revoke_badge(db, user, worker_id)
    return {"revoked": revoked_count}
