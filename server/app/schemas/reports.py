"""Pydantic schemas and serialization models for worker reports and device SOS.

Owns input validation, field constraints, role-segregated output schemas, and forbidden field isolation.
Must NOT query the database or enforce authorization checks.
"""
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class CreateReportRequest(BaseModel):
    """Payload to raise a worker hazard or SOS report."""
    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., pattern="^(injury|equipment_fault|gas_smell|need_help|sos)$")
    severity: str = Field("medium", pattern="^(low|medium|high|critical)$")
    tunnel_id: Optional[int] = None
    description: Optional[str] = Field(None, max_length=1000)


class UpdateReportRequest(BaseModel):
    """Payload to advance report status (superiors only)."""
    model_config = ConfigDict(extra="forbid")

    status: str = Field(..., pattern="^(open|acknowledged|resolved)$")
    resolution_note: Optional[str] = Field(None, max_length=500)


class ReportMineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str


class ReportTunnelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    name: str


class ReportUserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    full_name: str
    employee_no: Optional[str] = None


class ReportNodeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    node_no: int
    gateway_name: Optional[str] = None


class SuperiorReportView(BaseModel):
    """Full detailed report view for superiors, including device telemetry and actor names."""
    model_config = ConfigDict(extra="forbid")

    id: int
    source: str
    type: str
    severity: str
    description: Optional[str] = None
    status: str
    created_at: str
    last_signal_at: str
    repeat_count: int
    mine: ReportMineInfo
    tunnel: Optional[ReportTunnelInfo] = None
    reporter: Optional[ReportUserInfo] = None
    node: Optional[ReportNodeInfo] = None
    acknowledged_by_name: Optional[str] = None
    acknowledged_at: Optional[str] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None
    incident_id: Optional[int] = None


class WorkerReportView(BaseModel):
    """Strict worker-safe report view.

    Prevents leaking names of other workers, node identifiers, or gateway backhaul data.
    """
    model_config = ConfigDict(extra="forbid")

    id: int
    type: str
    severity: str
    description: Optional[str] = None
    status: str
    created_at: str
    acknowledged_at: Optional[str] = None
    resolved_at: Optional[str] = None
    tunnel_name: Optional[str] = None
