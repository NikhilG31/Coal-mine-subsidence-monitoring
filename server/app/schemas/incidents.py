"""Pydantic schemas for emergency incidents and worker roll call.

Owns serialization and deserialization of incident creation, roll call roster, and updates.
Must NOT execute database queries or perform authentication checks directly.
"""
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class IncidentCreateRequest(BaseModel):
    """Payload for opening a new evacuation or emergency incident."""
    model_config = ConfigDict(extra="forbid")

    mine_id: int
    tunnel_id: Optional[int] = None
    title: str = Field(..., min_length=3, max_length=120)
    level: int = Field(..., ge=1, le=3)
    siren: bool = False


class IncidentUpdateRequest(BaseModel):
    """Payload for closing an incident or updating siren state."""
    model_config = ConfigDict(extra="forbid")

    close: Optional[bool] = None
    close_note: Optional[str] = Field(None, max_length=500)
    confirm_unaccounted: Optional[bool] = None
    siren: Optional[bool] = None


class IncidentRosterItem(BaseModel):
    """Single worker roll call snapshot entry."""
    model_config = ConfigDict(extra="forbid")

    user_id: int
    full_name: str
    employee_no: Optional[str] = None
    added_at: str
    accounted_at: Optional[str] = None
    accounted_by_name: Optional[str] = None


class IncidentDetailResponse(BaseModel):
    """Detailed view of an incident with live headcount metrics."""
    model_config = ConfigDict(extra="forbid")

    id: int
    mine_id: int
    mine_name: Optional[str] = None
    tunnel_id: Optional[int] = None
    tunnel_name: Optional[str] = None
    source: str
    level: int
    title: str
    status: str
    siren_active: bool
    opened_by: Optional[int] = None
    opened_by_name: Optional[str] = None
    opened_at: str
    closed_at: Optional[str] = None
    closed_by: Optional[int] = None
    closed_by_name: Optional[str] = None
    close_note: Optional[str] = None
    unaccounted_count: int
    accounted_count: int
    total_count: int


class IncidentsListResponse(BaseModel):
    """List response envelope for emergency incidents."""
    model_config = ConfigDict(extra="forbid")

    incidents: List[IncidentDetailResponse]
