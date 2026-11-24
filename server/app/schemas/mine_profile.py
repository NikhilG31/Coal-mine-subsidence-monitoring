"""Pydantic schemas and serialization models for mine profiles, contacts, and production.

Owns input validation, extra-field forbidding, role-segregated profile models, and output projection.
Must NOT query the database or enforce authorization checks.
"""
from typing import List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class UpdateMineProfileRequest(BaseModel):
    """Payload to PATCH mine profile metadata (superiors only)."""
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=255)
    subsidiary: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None, pattern="^(active|maintenance|suspended|closed)$")
    method: Optional[str] = Field(None, pattern="^(opencast|underground|mixed)$")
    area_ha: Optional[float] = Field(None, ge=0)
    max_depth_m: Optional[float] = Field(None, ge=0)
    lat: Optional[float] = Field(None, ge=-90.0, le=90.0)
    lon: Optional[float] = Field(None, ge=-180.0, le=180.0)
    evacuation_notes: Optional[str] = Field(None, max_length=2000)


class ContactInputItem(BaseModel):
    """Single contact representation for replacing contacts."""
    model_config = ConfigDict(extra="forbid")

    role: str = Field(..., pattern="^(shift_supervisor|safety_officer|rescue_station|other)$")
    name: str = Field(..., min_length=1, max_length=255)
    phone: str = Field(..., min_length=5, max_length=32, pattern=r"^[0-9+() -]{5,32}$")
    rank: int = Field(..., ge=1, le=20)


class ReplaceContactsRequest(BaseModel):
    """Payload to replace all emergency contacts for a mine."""
    model_config = ConfigDict(extra="forbid")

    contacts: List[ContactInputItem] = Field(..., min_length=1, max_length=20)


class ProductionEntryInput(BaseModel):
    """Single daily production entry."""
    model_config = ConfigDict(extra="forbid")

    day: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    planned_t: Optional[float] = Field(None, ge=0)
    actual_t: Optional[float] = Field(None, ge=0)


class UpsertProductionRequest(BaseModel):
    """Payload to upsert daily production entries."""
    model_config = ConfigDict(extra="forbid")

    entries: List[ProductionEntryInput] = Field(..., min_length=1, max_length=62)


# =============================================================================
# Output Models
# =============================================================================

class WorkerContactView(BaseModel):
    """Worker contact view omitting internal database IDs."""
    model_config = ConfigDict(extra="forbid")

    role: str
    name: str
    phone: str
    rank: int


class WorkerTunnelView(BaseModel):
    """Worker tunnel view carrying current tunnel status."""
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: str


class WorkerMineProfileResponse(BaseModel):
    """Worker-safe mine profile view strictly omitting superior fields."""
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: str
    evacuation_notes: Optional[str] = None
    contacts: List[WorkerContactView]
    current_tunnel: Optional[WorkerTunnelView] = None


class SuperiorContactView(BaseModel):
    """Superior contact view with database ID."""
    model_config = ConfigDict(extra="forbid")

    id: int
    role: str
    name: str
    phone: str
    rank: int


class IdentityStatusView(BaseModel):
    """Mine identity and status group."""
    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    status: str
    lat: Optional[float] = None
    lon: Optional[float] = None
    evacuation_notes: Optional[str] = None


class CompanyClassificationView(BaseModel):
    """Mine company and classification group."""
    model_config = ConfigDict(extra="forbid")

    company_name: str
    subsidiary: Optional[str] = None
    method: str
    area_ha: Optional[float] = None
    max_depth_m: Optional[float] = None


class HeadcountTunnelItem(BaseModel):
    """Headcount breakdown for a single tunnel."""
    model_config = ConfigDict(extra="forbid")

    tunnel_id: int
    tunnel_name: str
    inside: int
    scheduled_now: int
    upcoming: int


class HeadcountTotalsItem(BaseModel):
    """Mine headcount totals."""
    model_config = ConfigDict(extra="forbid")

    inside: int
    scheduled_now: int
    upcoming: int


class HeadcountResponse(BaseModel):
    """Headcount response with mine totals and per-tunnel breakdown."""
    model_config = ConfigDict(extra="forbid")

    totals: HeadcountTotalsItem
    tunnels: List[HeadcountTunnelItem]


class ProductionSummaryView(BaseModel):
    """Monthly production summary figures."""
    model_config = ConfigDict(extra="forbid")

    month: str
    planned_total_t: float
    actual_total_t: float
    variance_t: float
    achievement_pct: Optional[float] = None
    reported_days: int


class SuperiorMineProfileResponse(BaseModel):
    """Superior full mine profile with all five groups."""
    model_config = ConfigDict(extra="forbid")

    identity_status: IdentityStatusView
    company_classification: CompanyClassificationView
    headcount: HeadcountResponse
    production: ProductionSummaryView
    contacts: List[SuperiorContactView]


class ProductionDayItem(BaseModel):
    """Daily production record item."""
    model_config = ConfigDict(extra="forbid")

    day: str
    planned_t: Optional[float] = None
    actual_t: Optional[float] = None
    updated_at: Optional[str] = None


class ProductionMonthResponse(BaseModel):
    """Monthly production list and summary response."""
    model_config = ConfigDict(extra="forbid")

    month: str
    days: List[ProductionDayItem]
    summary: ProductionSummaryView


class UpsertProductionResponse(BaseModel):
    """Response returned upon upserting production records."""
    model_config = ConfigDict(extra="forbid")

    updated: int
    summary: Union[ProductionSummaryView, List[ProductionSummaryView]]
    summaries: Optional[List[ProductionSummaryView]] = None
