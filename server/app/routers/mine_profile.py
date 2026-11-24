"""HTTP router for mine profiles, contacts hierarchy, headcount, and production tracking.

Owns /api/mines/{id}/profile, /api/mines/{id}/contacts, /api/mines/{id}/headcount, and /api/mines/{id}/production.
Must NOT execute raw database transactions directly, perform socket I/O, or leak data across roles.
"""
from typing import Optional, Union
import zoneinfo
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import HOURS_TIMEZONE
from app.database import get_db
from app.models.mine import Mine
from app.models.user import User
from app.schemas.mine_profile import (
    HeadcountResponse,
    ProductionMonthResponse,
    ReplaceContactsRequest,
    SuperiorMineProfileResponse,
    UpdateMineProfileRequest,
    UpsertProductionRequest,
    UpsertProductionResponse,
    WorkerMineProfileResponse,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
)
from app.security.roles import SUPERIOR_ROLES
from app.services import clock
from app.services.mine_contacts_service import replace_contacts
from app.services.mine_headcount_service import headcount_by_tunnel_and_shift
from app.services.mine_profile_service import (
    DuplicateMineNameError,
    build_superior_profile,
    build_worker_profile,
    update_profile,
)
from app.services.production_service import (
    list_days,
    month_summary,
    upsert_production,
    validate_month_str,
)

router = APIRouter(tags=["mine-profile"])


def _get_scoped_mine(db: Session, user: User, mine_id: int) -> Mine:
    """Returns the requested Mine if allowed by user scope, else raises HTTP 404."""
    allowed = get_allowed_mine_ids(db, user)
    if mine_id not in allowed:
        raise HTTPException(status_code=404, detail="Mine not found")

    mine = (
        db.query(Mine)
        .filter(Mine.id == mine_id, Mine.company_id == user.company_id)
        .first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Mine not found")
    return mine


# =============================================================================
# 1. GET PROFILE (ANY ROLE, SCOPED)
# =============================================================================

@router.get(
    "/api/mines/{id}/profile",
    response_model=Union[SuperiorMineProfileResponse, WorkerMineProfileResponse],
)
def api_get_mine_profile(
    id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns full profile for superiors or trimmed worker-safe model for workers."""
    mine = _get_scoped_mine(db, user, id)
    now = clock.utcnow()

    if user.role in SUPERIOR_ROLES:
        data = build_superior_profile(db, user, mine, now)
        return SuperiorMineProfileResponse(**data)
    else:
        data = build_worker_profile(db, user, mine, now)
        return WorkerMineProfileResponse(**data)


# =============================================================================
# 2. PATCH PROFILE (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.patch(
    "/api/mines/{id}/profile",
    response_model=SuperiorMineProfileResponse,
)
def api_patch_mine_profile(
    id: int,
    req: UpdateMineProfileRequest,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Updates mine metadata with PATCH semantics (superiors only)."""
    mine = _get_scoped_mine(db, user, id)
    fields = req.model_dump(exclude_unset=True)

    try:
        update_profile(db, user, mine, fields)
    except DuplicateMineNameError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    now = clock.utcnow()
    data = build_superior_profile(db, user, mine, now)
    return SuperiorMineProfileResponse(**data)


# =============================================================================
# 3. PUT CONTACTS (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.put("/api/mines/{id}/contacts")
def api_replace_contacts(
    id: int,
    req: ReplaceContactsRequest,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Replaces all emergency key contacts for a mine (superiors only)."""
    mine = _get_scoped_mine(db, user, id)
    contacts_data = [c.model_dump() for c in req.contacts]

    try:
        new_contacts = replace_contacts(db, user, mine, contacts_data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return {
        "contacts": [
            {
                "id": c.id,
                "role": c.role,
                "name": c.name,
                "phone": c.phone,
                "rank": c.rank,
            }
            for c in new_contacts
        ]
    }


# =============================================================================
# 4. GET HEADCOUNT (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.get("/api/mines/{id}/headcount", response_model=HeadcountResponse)
def api_get_mine_headcount(
    id: int,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns live headcount and shift lookahead for a mine (superiors only)."""
    mine = _get_scoped_mine(db, user, id)
    now = clock.utcnow()
    data = headcount_by_tunnel_and_shift(db, mine, now)
    return HeadcountResponse(**data)


# =============================================================================
# 5. GET PRODUCTION (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.get("/api/mines/{id}/production", response_model=ProductionMonthResponse)
def api_get_mine_production(
    id: int,
    month: Optional[str] = Query(None),
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns daily production records and monthly summary (superiors only)."""
    mine = _get_scoped_mine(db, user, id)

    if month is None:
        tz = zoneinfo.ZoneInfo(HOURS_TIMEZONE)
        month = clock.utcnow().astimezone(tz).strftime("%Y-%m")
    else:
        try:
            validate_month_str(month)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    days = list_days(db, mine.id, month)
    summary = month_summary(db, mine.id, month)

    return ProductionMonthResponse(
        month=month,
        days=days,
        summary=summary,
    )


# =============================================================================
# 6. PUT PRODUCTION (SUPERIORS ONLY, SCOPED)
# =============================================================================

@router.put("/api/mines/{id}/production", response_model=UpsertProductionResponse)
def api_upsert_mine_production(
    id: int,
    req: UpsertProductionRequest,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Upserts daily production entries (superiors only)."""
    mine = _get_scoped_mine(db, user, id)
    entries_data = [e.model_dump() for e in req.entries]

    try:
        result = upsert_production(db, user, mine, entries_data)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    return UpsertProductionResponse(**result)
