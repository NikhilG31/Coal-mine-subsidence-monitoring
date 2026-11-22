"""Service for retrieving latest sensor node telemetry readings.

Owns fetching the most recent Reading per node using PostgreSQL DISTINCT ON.
Must NOT execute database migrations or calculate hazard classification.
"""
from typing import Dict, Sequence
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from app.models.reading import Reading


def get_latest_readings(db: Session, node_ids: Sequence[int]) -> Dict[int, Reading]:
    """Retrieves the latest Reading entity for each given node_id.

    Uses the non-deprecated postgresql.distinct_on extension to eliminate SADeprecationWarning.
    """
    if not node_ids:
        return {}

    stmt = (
        select(Reading)
        .ext(distinct_on(Reading.node_id))
        .where(Reading.node_id.in_(list(node_ids)))
        .order_by(Reading.node_id, Reading.ts.desc())
    )
    readings = db.scalars(stmt).all()
    return {r.node_id: r for r in readings}
