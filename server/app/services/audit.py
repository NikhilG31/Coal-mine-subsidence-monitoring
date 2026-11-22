"""Audit logging service for operational and security event persistence.

Owns creating and attaching AuditLog rows to transactional database sessions.
Must NOT commit sessions autonomously, record passwords/secrets, or bypass company isolation.
"""
from typing import Any, Dict, Optional
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def record_audit(
    db: Session,
    user: Any,
    action: str,
    entity: str,
    entity_id: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
    company_id: Optional[int] = None,
) -> AuditLog:
    """Appends an audit record to the current transaction.

    The caller is responsible for committing the session so rolled-back changes
    do not leave orphaned audit records. Sensitive keys, hashes, and passwords
    must never be passed into the detail payload.
    """
    if company_id is None:
        company_id = getattr(user, "company_id", None)
    if company_id is None:
        raise ValueError("Cannot record audit log without an associated company_id")

    user_id = getattr(user, "id", None) if user else None

    # Sanitize detail payload: strip any accidental secret fields
    clean_detail = dict(detail or {})
    for forbidden_key in ("password", "api_key", "password_hash", "api_key_hash", "token", "new_password"):
        clean_detail.pop(forbidden_key, None)

    audit_entry = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        detail=clean_detail,
    )
    db.add(audit_entry)
    return audit_entry
