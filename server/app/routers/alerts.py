"""HTTP router for hazard and connectivity alerts.

Owns GET /api/alerts and POST /api/alerts/{alert_id}/ack endpoints with superior role authorization.
Must NOT deliver third-party webhooks or evaluate offline timeouts.
"""
import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.alert import Alert
from app.models.mine import Mine
from app.models.user import User
from app.security.dependencies import get_allowed_mine_ids, require_role
from app.security.roles import SUPERIOR_ROLES
from app.services.audit import record_audit
from app.services import clock

router = APIRouter(tags=["alerts"])


def _serialize_alert(alert: Alert) -> dict:
    """Serializes an alert entity for client responses without leaking forbidden fields."""
    return {
        "id": alert.id,
        "kind": alert.kind,
        "title": alert.title,
        "mine_id": alert.mine_id,
        "gateway_id": alert.gateway_id,
        "node_id": alert.node_id,
        "opened_at": alert.opened_at.isoformat() if alert.opened_at else None,
        "resolved_at": alert.resolved_at.isoformat() if alert.resolved_at else None,
        "acknowledged_at": alert.acknowledged_at.isoformat() if alert.acknowledged_at else None,
        "acknowledged_by_name": alert.acknowledged_by_user.full_name if alert.acknowledged_by_user else None,
    }


@router.get("/api/alerts")
def api_get_alerts(
    status_filter: str = Query("open", alias="status", pattern="^(open|all)$"),
    mine_id: Optional[int] = None,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Returns connectivity and hazard alerts scoped to permitted mines (superiors only)."""
    allowed_mines = get_allowed_mine_ids(db, user)

    if mine_id is not None:
        if mine_id not in allowed_mines:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Mine not found",
            )
        target_mines = [mine_id]
    else:
        target_mines = allowed_mines

    if not target_mines:
        return {"alerts": []}

    query = (
        db.query(Alert)
        .options(joinedload(Alert.acknowledged_by_user))
        .filter(Alert.mine_id.in_(target_mines))
    )

    if status_filter == "open":
        query = query.filter(Alert.resolved_at.is_(None))

    alerts_list = (
        query.order_by(Alert.opened_at.desc())
        .limit(200)
        .all()
    )

    return {"alerts": [_serialize_alert(a) for a in alerts_list]}


@router.post("/api/alerts/{alert_id}/ack")
def api_acknowledge_alert(
    alert_id: int,
    user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Acknowledges an alert idempotently within the caller's permitted mines."""
    allowed_mines = set(get_allowed_mine_ids(db, user))

    alert = (
        db.query(Alert)
        .options(joinedload(Alert.acknowledged_by_user))
        .filter(Alert.id == alert_id)
        .first()
    )

    if not alert:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    # Don't leak existence across mines or companies
    if alert.mine_id not in allowed_mines:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Alert not found",
        )

    if alert.acknowledged_at is None:
        now = clock.utcnow()
        alert.acknowledged_at = now
        alert.acknowledged_by = user.id
        record_audit(
            db=db,
            user=user,
            action="alert.acknowledge",
            entity="alert",
            entity_id=alert.id,
            detail={"alert_id": alert.id, "mine_id": alert.mine_id},
        )
        db.commit()
        db.refresh(alert)

    serialized = _serialize_alert(alert)
    resp = dict(serialized)
    resp["alert"] = serialized
    return resp
