"""Pydantic schemas for badges, gate check-in/out scans, headcount, and check flags.

Owns payload validation and output serialization for Step 6 gate management.
Must NOT execute database queries or compute rate limits.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class WorkerBadgeSummary(BaseModel):
    id: int
    full_name: str
    employee_no: Optional[str] = None


class BadgeIssueResponse(BaseModel):
    token: str
    badge_svg: str
    issued_at: str
    worker: WorkerBadgeSummary


class BadgeRevokeResponse(BaseModel):
    revoked: int


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=1)
    direction: str = Field(..., pattern="^(in|out)$")
    tunnel_id: Optional[int] = None


class ManualScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    direction: str = Field(..., pattern="^(in|out)$")
    tunnel_id: Optional[int] = None
    note: Optional[str] = None


class TunnelSummaryView(BaseModel):
    id: int
    name: str


class ScanWarningItem(BaseModel):
    code: str
    message: str


class ScanResponse(BaseModel):
    duplicate: bool
    worker: WorkerBadgeSummary
    direction: str
    tunnel: TunnelSummaryView
    scanned_at: str
    warnings: List[ScanWarningItem] = []


class ScannerSummaryView(BaseModel):
    id: int
    full_name: str


class CheckEventItem(BaseModel):
    id: int
    worker: WorkerBadgeSummary
    tunnel: TunnelSummaryView
    direction: str
    method: str
    scanned_at: str
    scanner: Optional[ScannerSummaryView] = None


class CheckEventsListResponse(BaseModel):
    events: List[CheckEventItem]


class HeadcountWorkerItem(BaseModel):
    id: int
    full_name: str
    employee_no: Optional[str] = None
    scanned_at: str


class TunnelHeadcountItem(BaseModel):
    tunnel_id: int
    inside: int
    scheduled_now: int
    workers: List[HeadcountWorkerItem] = []


class HeadcountResponse(BaseModel):
    headcount: Dict[str, TunnelHeadcountItem]


class AssignmentFlagSummary(BaseModel):
    id: int
    starts_at: str
    ends_at: str
    tunnel: TunnelSummaryView


class CheckFlagItem(BaseModel):
    id: int
    worker: WorkerBadgeSummary
    assignment: AssignmentFlagSummary
    raised_at: str
    overdue: bool
    cleared_at: Optional[str] = None
    cleared_by_name: Optional[str] = None
    note: Optional[str] = None


class CheckFlagsListResponse(BaseModel):
    flags: List[CheckFlagItem]


class ClearFlagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(..., min_length=3, max_length=500)
