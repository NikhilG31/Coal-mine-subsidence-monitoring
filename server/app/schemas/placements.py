"""Pydantic schemas for node placement endpoints.

Owns WorkerPlacementView, SuperiorPlacementView, and PlacementsResponse schemas.
Must NOT execute database queries or perform session transactions.
"""
from typing import List, Optional, Union
from pydantic import BaseModel, ConfigDict


class WorkerPlacementView(BaseModel):
    """Restricted placement view model for workers (foreman, miner)."""
    model_config = ConfigDict(extra="forbid")

    node_id: int
    location_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    placed_at: Optional[float] = None


class SuperiorPlacementView(BaseModel):
    """Placement view model for superiors (manager, safety officer, engineer)."""
    model_config = ConfigDict(extra="forbid")

    node_id: int
    location_name: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    placed_at: Optional[float] = None
    gateway_mac_address: str
    placed_by: Optional[int] = None


class PlacementsResponse(BaseModel):
    """Response container for GET /api/placements."""
    placements: List[Union[WorkerPlacementView, SuperiorPlacementView]]
