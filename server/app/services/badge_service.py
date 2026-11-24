"""Worker QR badge management service.

Owns cryptographic badge token generation, SHA-256 hash storage, QR SVG rendering, and revocation.
Must NOT handle HTTP requests, import FastAPI components, or store/log plaintext tokens.
"""
import hashlib
import secrets
from typing import Optional, Tuple
import segno
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.qr_token import QRToken
from app.models.user import User
from app.security.roles import ROLE_FOREMAN, ROLE_MINER
from app.services.audit import record_audit
from app.services import clock


class BadgeError(Exception):
    """Exception raised for badge issuance or revocation violations."""

    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def issue_badge(
    db: Session,
    actor: User,
    worker_id: int,
) -> Tuple[str, str, QRToken]:
    """Issues a new QR badge for a worker and revokes previous active tokens.

    Locks the worker row with SELECT ... FOR UPDATE. Flushes after revoking old token
    and before inserting new one. Converts IntegrityError on uq_qr_tokens_one_active into 409 badge_busy.
    """
    worker = (
        db.query(User)
        .filter(User.id == worker_id, User.company_id == actor.company_id)
        .with_for_update()
        .first()
    )
    if not worker:
        raise BadgeError("Worker not found", status_code=404)
    if worker.role not in (ROLE_FOREMAN, ROLE_MINER) or not worker.active:
        raise BadgeError("Badge can only be issued to an active foreman or miner", status_code=400)

    now = clock.utcnow()

    # Revoke any currently active badges for this worker
    active_tokens = (
        db.query(QRToken)
        .filter(QRToken.user_id == worker.id, QRToken.revoked_at.is_(None))
        .all()
    )
    for tok in active_tokens:
        tok.revoked_at = now
        tok.revoked_by = actor.id

    # Flush after revoking old token and before inserting new one
    db.flush()

    # Generate random 256-bit badge token
    plaintext_token = f"cmb_{secrets.token_urlsafe(32)}"
    token_hash = hashlib.sha256(plaintext_token.encode("utf-8")).hexdigest()

    qr_token = QRToken(
        user_id=worker.id,
        token_hash=token_hash,
        issued_at=now,
        issued_by=actor.id,
    )
    db.add(qr_token)

    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise BadgeError("Badge operation in progress", status_code=409, code="badge_busy")

    # Render QR SVG using segno
    qr = segno.make(plaintext_token, error="m")
    svg_content = qr.svg_inline(scale=6)

    # Audit log (NEVER store or log plaintext token or hash)
    record_audit(
        db,
        actor,
        action="badge.issue",
        entity="worker",
        entity_id=worker.id,
        detail={"worker_id": worker.id},
    )

    db.commit()
    db.refresh(qr_token)

    return plaintext_token, svg_content, qr_token


def revoke_badge(
    db: Session,
    actor: User,
    worker_id: int,
) -> int:
    """Revokes active badges for a worker and records an audit entry.

    Verifies the worker belongs to the actor's company inside the service (404 otherwise).
    """
    worker = (
        db.query(User)
        .filter(User.id == worker_id, User.company_id == actor.company_id)
        .first()
    )
    if not worker or worker.role not in (ROLE_FOREMAN, ROLE_MINER):
        raise BadgeError("Worker not found", status_code=404)

    now = clock.utcnow()
    active_tokens = (
        db.query(QRToken)
        .filter(QRToken.user_id == worker_id, QRToken.revoked_at.is_(None))
        .all()
    )
    count = len(active_tokens)
    for tok in active_tokens:
        tok.revoked_at = now
        tok.revoked_by = actor.id

    if count > 0:
        record_audit(
            db,
            actor,
            action="badge.revoke",
            entity="worker",
            entity_id=worker_id,
            detail={"worker_id": worker_id},
        )

    db.commit()
    return count
