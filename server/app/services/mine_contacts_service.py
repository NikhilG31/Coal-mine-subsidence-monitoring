"""Mine emergency contacts management service.

Owns full replacement, validation, call hierarchy ranking, and audit logging of key mine contacts.
Must NOT initiate external phone calls, format API responses, or bypass mine scoping.
"""
import re
from typing import Any, Dict, List
from sqlalchemy.orm import Session

from app.models.mine import Mine
from app.models.mine_contact import MineContact
from app.services.audit import record_audit

_PHONE_RE = re.compile(r"^[0-9+() -]{5,32}$")
_VALID_ROLES = {"shift_supervisor", "safety_officer", "rescue_station", "other"}


def replace_contacts(
    db: Session,
    actor: Any,
    mine: Mine,
    contacts: List[Dict[str, Any]],
) -> List[MineContact]:
    """Atomically validates and replaces all emergency contacts for a mine.

    Validation rules per B3:
    - 1-20 items
    - ranks unique and between 1 and 20
    - role enum: shift_supervisor, safety_officer, rescue_station, other
    - name: 1-255 characters trimmed
    - phone: matches ^[0-9+() -]{5,32}$

    Atomic replacement:
    - Deletes existing contacts and inserts new in one transaction with flush
    - Audits mine.contacts_update with detail {count} only (never names or numbers)
    """
    if not isinstance(contacts, list) or len(contacts) < 1 or len(contacts) > 20:
        raise ValueError("Contacts list must contain between 1 and 20 items")

    ranks_seen = set()
    validated = []

    for idx, item in enumerate(contacts):
        if not isinstance(item, dict):
            raise ValueError(f"Contact item at index {idx} must be an object")

        role = item.get("role")
        if role not in _VALID_ROLES:
            raise ValueError(
                f"Invalid contact role '{role}'. Must be one of: {sorted(list(_VALID_ROLES))}"
            )

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Contact name must be a non-empty string between 1 and 255 characters")
        name = name.strip()
        if len(name) > 255:
            raise ValueError("Contact name cannot exceed 255 characters")

        phone = item.get("phone")
        if not isinstance(phone, str) or not _PHONE_RE.match(phone.strip()):
            raise ValueError(
                f"Contact phone '{phone}' is invalid. Must match ^[0-9+() -]{{5,32}}$"
            )
        phone = phone.strip()

        rank = item.get("rank")
        try:
            rank_int = int(rank)
        except (ValueError, TypeError):
            raise ValueError(f"Contact rank must be an integer between 1 and 20 (got {rank})")
        if rank_int < 1 or rank_int > 20:
            raise ValueError(f"Contact rank {rank_int} out of range (must be 1..20)")
        if rank_int in ranks_seen:
            raise ValueError(f"Duplicate contact rank {rank_int} in contacts payload")
        ranks_seen.add(rank_int)

        validated.append({
            "role": role,
            "name": name,
            "phone": phone,
            "rank": rank_int,
        })

    # Sort by rank
    validated.sort(key=lambda c: c["rank"])

    # Atomic replace in ONE transaction with a flush between
    db.query(MineContact).filter(MineContact.mine_id == mine.id).delete(synchronize_session=False)
    db.flush()

    new_contacts = []
    for item in validated:
        contact = MineContact(
            mine_id=mine.id,
            role=item["role"],
            name=item["name"],
            phone=item["phone"],
            rank=item["rank"],
        )
        db.add(contact)
        new_contacts.append(contact)

    db.flush()

    record_audit(
        db=db,
        user=actor,
        action="mine.contacts_update",
        entity="mine",
        entity_id=mine.id,
        detail={"count": len(new_contacts)},
        company_id=mine.company_id,
    )
    db.commit()

    return new_contacts
