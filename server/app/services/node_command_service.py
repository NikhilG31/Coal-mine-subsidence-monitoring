"""Sensor node downlink hardware command domain service.

Owns creating and sequencing node commands, checking pending commands for gateways, and tracking acks.
Must NOT handle HTTP requests, import FastAPI components, or mutate reading packets.
"""
import datetime
from typing import Optional
from sqlalchemy.orm import Session

from app.config import COMMAND_TTL_S
from app.models.node import Node
from app.models.node_command import NodeCommand
from app.models.user import User
from app.services.audit import record_audit
from app.services import clock


class NodeCommandError(Exception):
    """Base exception for node command business logic errors."""
    def __init__(self, detail: str, status_code: int = 400, code: Optional[str] = None):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def create_command(
    db: Session,
    actor: Optional[User],
    node: Node,
    cmd: str,
    now: Optional[datetime.datetime] = None,
) -> NodeCommand:
    """Creates a new downlink command for a sensor node, expiring older pending commands.

    B0: Locks node row, expires older pending command, calculates next seq (1..65535, wrapping).
    Audits 'node.command' with detail {node_id, cmd, seq}.
    """
    valid_cmds = ("buzzer_on", "buzzer_off", "resume")
    if cmd not in valid_cmds:
        raise NodeCommandError(f"Invalid command '{cmd}'. Must be one of {valid_cmds}")

    if now is None:
        now = clock.utcnow()

    # 1. Lock the node row to serialize concurrent command creation
    db.query(Node).filter(Node.id == node.id).with_for_update().first()

    # 2. Expire any currently pending command for this node
    older_pending = (
        db.query(NodeCommand)
        .filter(
            NodeCommand.node_id == node.id,
            NodeCommand.acked_at.is_(None),
            NodeCommand.expires_at > now,
        )
        .all()
    )
    for p in older_pending:
        p.expires_at = now
    if older_pending:
        db.flush()

    # 3. Calculate next seq (1..65535, wrapping, never 0)
    newest = (
        db.query(NodeCommand)
        .filter(NodeCommand.node_id == node.id)
        .order_by(NodeCommand.created_at.desc(), NodeCommand.id.desc())
        .first()
    )
    if newest is None:
        next_seq = 1
    else:
        next_seq = (newest.seq % 65535) + 1

    # 4. Insert new command row
    expires_at = now + datetime.timedelta(seconds=COMMAND_TTL_S)
    cmd_obj = NodeCommand(
        node_id=node.id,
        cmd=cmd,
        seq=next_seq,
        created_by=actor.id if actor else None,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(cmd_obj)
    db.flush()

    # 5. Record audit action
    company_id = 0
    if node.gateway and node.gateway.mine:
        company_id = node.gateway.mine.company_id
    elif node.tunnel and node.tunnel.mine:
        company_id = node.tunnel.mine.company_id
    elif actor and actor.company_id:
        company_id = actor.company_id

    record_audit(
        db=db,
        user=actor,
        action="node.command",
        entity="node_command",
        entity_id=cmd_obj.id,
        detail={"node_id": node.id, "cmd": cmd, "seq": next_seq},
        company_id=company_id,
    )

    return cmd_obj


def pending_lines_for_gateway(
    db: Session,
    gateway_id: int,
    now: Optional[datetime.datetime] = None,
) -> str:
    """Returns formatted plain-text command lines for all pending commands under a gateway.

    Format: CMD,<node_no>,<CMD_UPPER>,<seq>\\n
    """
    if now is None:
        now = clock.utcnow()

    # Query active pending commands for placed nodes under this gateway
    rows = (
        db.query(Node.node_no, NodeCommand.cmd, NodeCommand.seq)
        .join(Node, NodeCommand.node_id == Node.id)
        .filter(
            Node.gateway_id == gateway_id,
            NodeCommand.acked_at.is_(None),
            NodeCommand.expires_at > now,
        )
        .order_by(Node.node_no.asc(), NodeCommand.created_at.desc())
        .all()
    )

    if not rows:
        return ""

    # At most one command per node (newest unexpired)
    seen_nodes = set()
    lines = []
    for node_no, cmd, seq in rows:
        if node_no in seen_nodes:
            continue
        seen_nodes.add(node_no)
        lines.append(f"CMD,{node_no},{cmd.upper()},{seq}\n")

    return "".join(lines)


def mark_acked(
    db: Session,
    node: Node,
    last_cmd_seq: int,
    now: Optional[datetime.datetime] = None,
) -> Optional[NodeCommand]:
    """Finds pending command whose seq equals last_cmd_seq and marks it acked."""
    if last_cmd_seq <= 0 or last_cmd_seq > 65535:
        return None

    if now is None:
        now = clock.utcnow()

    cmd_obj = (
        db.query(NodeCommand)
        .filter(
            NodeCommand.node_id == node.id,
            NodeCommand.seq == last_cmd_seq,
            NodeCommand.acked_at.is_(None),
        )
        .order_by(NodeCommand.created_at.desc())
        .first()
    )
    if cmd_obj:
        cmd_obj.acked_at = now
        db.flush()
        return cmd_obj

    return None
