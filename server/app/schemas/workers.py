"""Pydantic schemas for workers, profiles, and shift assignments.

Owns input validation for profiles, shift scheduling, and output representations.
Must NOT execute database queries or perform scheduling conflict resolution.
"""
import datetime
import re
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


PHONE_REGEX = re.compile(r"^[0-9+() -]{5,32}$")


def _validate_tz_aware(v: Any) -> datetime.datetime:
    if isinstance(v, str):
        try:
            dt = datetime.datetime.fromisoformat(v)
        except ValueError as err:
            raise ValueError(f"Invalid ISO-8601 datetime string: {err}") from err
    elif isinstance(v, datetime.datetime):
        dt = v
    else:
        raise ValueError("Value must be an ISO-8601 datetime string")

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("Datetime must include timezone offset (e.g. +05:30 or Z)")

    return dt.astimezone(datetime.timezone.utc)


def _validate_phone_field(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not PHONE_REGEX.match(v):
        raise ValueError("Phone number must contain 5-32 digits, spaces, plus signs, or hyphens")
    return v


class ProfileUpsertRequest(BaseModel):
    """Payload for creating or updating a worker's personal and emergency profile."""

    employee_no: str = Field(..., min_length=1, max_length=64)
    phone: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    joined_on: Optional[datetime.date] = None

    @field_validator("employee_no")
    @classmethod
    def validate_employee_no(cls, v: str) -> str:
        trimmed = v.strip()
        if not (1 <= len(trimmed) <= 64):
            raise ValueError("Employee number must be between 1 and 64 characters")
        return trimmed

    @field_validator("phone", "emergency_contact_phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        return _validate_phone_field(v)

    @field_validator("emergency_contact_name")
    @classmethod
    def validate_contact_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        trimmed = v.strip()
        return trimmed if trimmed else None


class CreateAssignmentRequest(BaseModel):
    """Payload for scheduling a worker to an underground tunnel shift."""

    user_id: int
    tunnel_id: int
    starts_at: datetime.datetime
    ends_at: datetime.datetime

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def validate_datetimes(cls, v: Any) -> datetime.datetime:
        return _validate_tz_aware(v)


class UpdateAssignmentRequest(BaseModel):
    """Payload for shortening an existing shift assignment end time."""

    ends_at: datetime.datetime

    @field_validator("ends_at", mode="before")
    @classmethod
    def validate_end_time(cls, v: Any) -> datetime.datetime:
        return _validate_tz_aware(v)


class CurrentTunnelView(BaseModel):
    id: int
    name: str
    mine_id: int


class WorkerSummaryView(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    active: bool
    employee_no: Optional[str] = None
    current_tunnel: Optional[CurrentTunnelView] = None


class WorkersListResponse(BaseModel):
    workers: List[WorkerSummaryView]


class ShiftSummaryView(BaseModel):
    id: int
    tunnel_id: int
    tunnel_name: str
    mine_id: int
    mine_name: str
    starts_at: str
    ends_at: str
    created_by: Optional[int] = None
    created_at: str


class WorkerDetailResponse(BaseModel):
    id: int
    username: str
    full_name: str
    role: str
    active: bool
    employee_no: Optional[str] = None
    phone: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    joined_on: Optional[str] = None
    current_tunnel: Optional[CurrentTunnelView] = None
    upcoming_shifts: List[ShiftSummaryView]
    past_shifts: List[ShiftSummaryView]


class AssignmentDetailView(BaseModel):
    id: int
    user_id: int
    worker_name: str
    employee_no: Optional[str] = None
    tunnel_id: int
    tunnel_name: str
    mine_id: int
    mine_name: str
    starts_at: str
    ends_at: str
    created_by: Optional[int] = None
    created_at: str


class CreateAssignmentResponse(BaseModel):
    assignment: AssignmentDetailView
    warnings: List[dict]


class AssignmentsListResponse(BaseModel):
    assignments: List[AssignmentDetailView]


class WorkerShiftView(BaseModel):
    """Worker-safe view of an assignment with created_by omitted."""

    id: int
    tunnel_id: int
    tunnel_name: str
    mine_id: int
    mine_name: str
    starts_at: str
    ends_at: str
    created_at: str


class WorkerShiftsResponse(BaseModel):
    shifts: List[WorkerShiftView]
