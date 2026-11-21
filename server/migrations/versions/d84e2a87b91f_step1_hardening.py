"""step1_hardening

Revision ID: d84e2a87b91f
Revises: c79d6f612a5e
Create Date: 2026-11-22 02:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd84e2a87b91f'
down_revision: Union[str, Sequence[str], None] = 'c79d6f612a5e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add server defaults for NOT NULL columns with Python-side defaults
    op.alter_column('mines', 'status', server_default=sa.text("'active'"))
    op.alter_column('mines', 'method', server_default=sa.text("'underground'"))
    op.alter_column('tunnels', 'status', server_default=sa.text("'active'"))
    op.alter_column('users', 'active', server_default=sa.text('true'))
    op.alter_column('users', 'force_pw_change', server_default=sa.text('false'))
    op.alter_column('gateways', 'active', server_default=sa.text('true'))
    op.alter_column('nodes', 'status', server_default=sa.text("'normal'"))
    op.alter_column('nodes', 'fast_mode', server_default=sa.text('false'))
    op.alter_column('nodes', 'buzzer_on', server_default=sa.text('false'))
    op.alter_column('nodes', 'last_cmd_seq', server_default=sa.text('0'))
    op.alter_column('readings', 'boot_id', server_default=sa.text('0'))

    # 2. Drop redundant index ix_readings_node_id (covered by ix_readings_node_id_ts)
    op.drop_index(op.f('ix_readings_node_id'), table_name='readings')

    # 3. Add CHECK constraint on nodes.node_no
    op.create_check_constraint('ck_nodes_node_no_range', 'nodes', 'node_no BETWEEN 1 AND 255')


def downgrade() -> None:
    # 1. Drop CHECK constraint on nodes.node_no
    op.drop_constraint('ck_nodes_node_no_range', 'nodes', type_='check')

    # 2. Re-create index ix_readings_node_id
    op.create_index(op.f('ix_readings_node_id'), 'readings', ['node_id'], unique=False)

    # 3. Remove server defaults
    op.alter_column('readings', 'boot_id', server_default=None)
    op.alter_column('nodes', 'last_cmd_seq', server_default=None)
    op.alter_column('nodes', 'buzzer_on', server_default=None)
    op.alter_column('nodes', 'fast_mode', server_default=None)
    op.alter_column('nodes', 'status', server_default=None)
    op.alter_column('gateways', 'active', server_default=None)
    op.alter_column('users', 'force_pw_change', server_default=None)
    op.alter_column('users', 'active', server_default=None)
    op.alter_column('tunnels', 'status', server_default=None)
    op.alter_column('mines', 'method', server_default=None)
    op.alter_column('mines', 'status', server_default=None)
