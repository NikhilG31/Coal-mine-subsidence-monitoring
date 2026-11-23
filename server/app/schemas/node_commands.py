"""Pydantic schemas for downlink hardware node commands.

Owns serialization and deserialization of node commands creation, query filters, and status.
Must NOT execute database queries or perform authentication checks directly.
"""
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CreateNodeCommandRequest(BaseModel):
    """Payload to dispatch a downlink command to a sensor node."""
    model_config = ConfigDict(extra="forbid")

    cmd: str = Field(..., pattern="^(buzzer_on|buzzer_off|resume)$")


class NodeCommandResponse(BaseModel):
    """Response returned upon creating a new downlink command."""
    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: int
    cmd: str
    seq: int
    expires_at: str
    status: str = "pending"


class NodeCommandItem(BaseModel):
    """Single node command history item with ack and status info."""
    model_config = ConfigDict(extra="forbid")

    id: int
    node_id: int
    cmd: str
    seq: int
    created_at: str
    expires_at: str
    acked_at: Optional[str] = None
    status: str  # "pending" | "acked" | "expired" | "superseded"


class NodeCommandsListResponse(BaseModel):
    """List response envelope for node commands."""
    model_config = ConfigDict(extra="forbid")

    commands: List[NodeCommandItem]
