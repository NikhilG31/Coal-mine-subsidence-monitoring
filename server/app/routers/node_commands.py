"""HTTP router for sensor node downlink hardware commands.

Owns REST endpoints for creating buzzer commands and inspecting command history and status.
Must NOT execute raw binary serializations or manipulate database schemas.
"""
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.node import Node
from app.models.node_command import NodeCommand
from app.models.tunnel import Tunnel
from app.models.user import User
from app.schemas.node_commands import (
    CreateNodeCommandRequest,
    NodeCommandItem,
    NodeCommandResponse,
    NodeCommandsListResponse,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    get_current_user,
    require_role,
)
from app.security.roles import SUPERIOR_ROLES
from app.services import clock
from app.services.node_command_service import create_command

router = APIRouter(prefix="/api/nodes", tags=["node_commands"])


def _get_scoped_node(node_id: int, user: User, db: Session) -> Node:
    """Verifies node existence and tenant/mine scoping for the user."""
    node = (
        db.query(Node)
        .options(joinedload(Node.tunnel).joinedload(Tunnel.mine))
        .filter(Node.id == node_id)
        .first()
    )
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    if node.tunnel and node.tunnel.mine:
        if node.tunnel.mine.company_id != user.company_id:
            raise HTTPException(status_code=404, detail="Node not found")
        allowed_mines = get_allowed_mine_ids(db, user)
        if allowed_mines is not None and node.tunnel.mine_id not in allowed_mines:
            raise HTTPException(status_code=404, detail="Node not found")

    return node


@router.post("/{node_id}/commands", status_code=status.HTTP_201_CREATED, response_model=NodeCommandResponse)
def dispatch_command(
    node_id: int,
    req: CreateNodeCommandRequest,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: creates a downlink command for a sensor node."""
    now = clock.utcnow()
    node = _get_scoped_node(node_id, current_user, db)

    cmd_obj = create_command(db=db, actor=current_user, node=node, cmd=req.cmd, now=now)
    db.commit()
    db.refresh(cmd_obj)

    return NodeCommandResponse(
        id=cmd_obj.id,
        node_id=node.id,
        cmd=cmd_obj.cmd,
        seq=cmd_obj.seq,
        expires_at=cmd_obj.expires_at.astimezone(datetime.timezone.utc).isoformat(),
    )


@router.get("/{node_id}/commands", response_model=NodeCommandsListResponse)
def list_node_commands(
    node_id: int,
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: lists recent command history and delivery status for a sensor node."""
    now = clock.utcnow()
    node = _get_scoped_node(node_id, current_user, db)

    commands = (
        db.query(NodeCommand)
        .filter(NodeCommand.node_id == node.id)
        .order_by(NodeCommand.created_at.desc(), NodeCommand.id.desc())
        .limit(limit)
        .all()
    )

    items = []
    for c in commands:
        if c.acked_at is not None:
            c_status = "acked"
        elif c.expires_at <= now:
            c_status = "expired"
        else:
            c_status = "pending"

        items.append(
            NodeCommandItem(
                id=c.id,
                node_id=c.node_id,
                cmd=c.cmd,
                seq=c.seq,
                created_at=c.created_at.astimezone(datetime.timezone.utc).isoformat(),
                expires_at=c.expires_at.astimezone(datetime.timezone.utc).isoformat(),
                acked_at=c.acked_at.astimezone(datetime.timezone.utc).isoformat() if c.acked_at else None,
                status=c_status,
            )
        )

    return NodeCommandsListResponse(commands=items)
