"""Pydantic schemas for sensor node telemetry responses.

Owns WorkerNodeView and SuperiorNodeView response models preventing field leakage.
Must NOT execute database queries or perform session transactions.
"""
from typing import Any, List, Optional, Union
from pydantic import BaseModel, ConfigDict


class WorkerNodeView(BaseModel):
    """Restricted view model for workers (foreman, miner)."""
    model_config = ConfigDict(extra="forbid")

    node_id: int
    location_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: str
    color: str
    tilt_x: float
    tilt_y: float
    vibration_magnitude: float
    vibration_detected: bool
    battery_percentage: float
    low_battery: bool
    online: bool
    age_s: float


class SuperiorNodeView(BaseModel):
    """Complete diagnostic view model for superiors (manager, safety officer, engineer)."""
    gateway_mac_address: str
    gateway: str
    node_id: int
    counter: int
    tilt_x: float
    tilt_y: float
    vibration_magnitude: float
    vibration_detected: bool
    battery_voltage: float
    battery_percentage: float
    status: str
    score: float
    reasons: List[str]
    color: str
    low_battery: bool
    age_s: float
    online: bool
    placed: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_name: str


class NodesResponse(BaseModel):
    """Container schema for the GET /api/nodes endpoint."""
    now: float
    nodes: List[Union[WorkerNodeView, SuperiorNodeView, dict]]
