"""HTTP router for sensor node downlink hardware commands.

Owns REST endpoints for creating buzzer commands and inspecting command history and status.
Must NOT execute raw binary serializations or manipulate database schemas.
"""
import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.config import COMMAND_TTL_S
from app.database import get_db
from app.models.gateway import Gateway
from app.models.node import Node
from app.models.node_command import NodeCommand
from app.models.user import User
from app.schemas.node_commands import (
    CreateNodeCommandRequest,
    NodeCommandItem,
    NodeCommandResponse,
    NodeCommandsListResponse,
)
from app.security.dependencies import (
    get_allowed_mine_ids,
    require_role,
)
from app.security.roles import SUPERIOR_ROLES
from app.services import clock
from app.services.node_command_service import create_command
from app.services.node_placement import is_placed

router = APIRouter(prefix="/api/nodes", tags=["node_commands"])


def _get_scoped_node(node_id: int, user: User, db: Session) -> Node:
    """Verifies node existence and tenant/mine scoping through its gateway.

    A1: Scope a node through its GATEWAY, never through its tunnel:
    node -> gateway -> gateway.mine -> mine.company_id must equal the user's company,
    and gateway.mine_id must be in get_allowed_mine_ids; otherwise 404 identical to "not found",
    for placed AND unplaced nodes.
    """
    node = (
        db.query(Node)
        .options(joinedload(Node.gateway).joinedload(Gateway.mine))
        .filter(Node.id == node_id)
        .first()
    )
    if not node or not node.gateway or not node.gateway.mine:
        raise HTTPException(status_code=404, detail="Node not found")

    if node.gateway.mine.company_id != user.company_id:
        raise HTTPException(status_code=404, detail="Node not found")

    allowed_mines = get_allowed_mine_ids(db, user)
    if allowed_mines is not None and node.gateway.mine_id not in allowed_mines:
        raise HTTPException(status_code=404, detail="Node not found")

    return node


@router.post("/{node_id}/commands", status_code=status.HTTP_201_CREATED, response_model=NodeCommandResponse)
def dispatch_command(
    node_id: int,
    req: CreateNodeCommandRequest,
    current_user: User = Depends(require_role(*SUPERIOR_ROLES)),
    db: Session = Depends(get_db),
):
    """Superiors only: creates a downlink command for a sensor node.

    A1: POST .../commands requires the node to be PLACED (is_placed) and its gateway active,
    else 404 {"detail": "Node not found"}.
    """
    now = clock.utcnow()
    node = _get_scoped_node(node_id, current_user, db)

    if not is_placed(node) or not node.gateway.active:
        raise HTTPException(status_code=404, detail="Node not found")

    cmd_obj = create_command(db=db, actor=current_user, node=node, cmd=req.cmd, now=now)
    db.commit()
    db.refresh(cmd_obj)

    return NodeCommandResponse(
        id=cmd_obj.id,
        node_id=node.id,
        cmd=cmd_obj.cmd,
        seq=cmd_obj.seq,
        expires_at=cmd_obj.expires_at.astimezone(datetime.timezone.utc).isoformat(),
        status="pending",
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
    ttl_delta = datetime.timedelta(seconds=COMMAND_TTL_S)
    for c in commands:
        if c.acked_at is not None:
            c_status = "acked"
        elif c.expires_at < (c.created_at + ttl_delta):
            c_status = "superseded"
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
