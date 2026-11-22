"""HTTP router for mine worker discovery, inspection, and profile management.

Owns GET /api/workers, GET /api/workers/{id}, and PUT /api/workers/{id}/profile endpoints.
Must NOT perform low-level shift arithmetic, bypass mine scoping, or leak phone numbers into audit logs.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.worker_profile import WorkerProfile
from app.schemas.workers import (
    ProfileUpsertRequest,
    WorkerDetailResponse,
    WorkersListResponse,
)
from app.security.dependencies import get_current_user, require_role, security_logger
from app.security.roles import ROLE_FOREMAN, ROLE_MANAGER, ROLE_MINER, SUPERIOR_ROLES
from app.services.audit import record_audit
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
    worker = (
        db.query(User)
        .filter(
            User.id == worker_id,
            User.company_id == user.company_id,
            User.role.in_([ROLE_FOREMAN, ROLE_MINER]),
        )
        .first()
    )
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    profile = db.query(WorkerProfile).filter(WorkerProfile.user_id == worker_id).first()

    changed_fields = []
    if profile is None:
        changed_fields = ["employee_no", "phone", "emergency_contact_name", "emergency_contact_phone", "joined_on"]
        profile = WorkerProfile(
            user_id=worker.id,
            company_id=worker.company_id,
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
        entity_id=worker.id,
        detail={"changed_fields": changed_fields},
    )
    db.commit()
    db.refresh(profile)

    security_logger.info(
        "Manager '%s' updated profile for worker %d (%s)",
        user.username,
        worker.id,
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
