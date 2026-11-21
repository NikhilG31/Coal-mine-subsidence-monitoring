"""User role definitions and role group helpers.

Owns the role enumeration, role groups (superiors and workers), and group mapping logic.
Must NOT define route endpoints or execute database queries.
"""
from typing import Set

ROLE_MANAGER: str = "manager"
ROLE_SAFETY_OFFICER: str = "safety_officer"
ROLE_ENGINEER: str = "engineer"
ROLE_FOREMAN: str = "foreman"
ROLE_MINER: str = "miner"

SUPERIOR_ROLES: Set[str] = {
    ROLE_MANAGER,
    ROLE_SAFETY_OFFICER,
    ROLE_ENGINEER,
}

WORKER_ROLES: Set[str] = {
    ROLE_FOREMAN,
    ROLE_MINER,
}

ALL_ROLES: Set[str] = SUPERIOR_ROLES | WORKER_ROLES


def role_group(role: str) -> str:
    """Returns 'superior' or 'worker' for a given role, or raises ValueError."""
    if role in SUPERIOR_ROLES:
        return "superior"
    if role in WORKER_ROLES:
        return "worker"
    raise ValueError(f"Unknown user role: '{role}'")
