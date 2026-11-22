"""step3_placement_and_audit

Revision ID: e95f3b721a01
Revises: d84e2a87b91f
Create Date: 2026-11-22 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e95f3b721a01'
down_revision: Union[str, Sequence[str], None] = 'd84e2a87b91f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create audit_log table and index
    op.create_table(
        'audit_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('ts', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('entity', sa.String(length=64), nullable=False),
        sa.Column('entity_id', sa.Integer(), nullable=True),
        sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_audit_log_company_id_ts',
        'audit_log',
        ['company_id', sa.text('ts DESC')],
        unique=False,
    )
    op.create_index(
        op.f('ix_audit_log_company_id'),
        'audit_log',
        ['company_id'],
        unique=False,
    )

    # 2. Add unique constraint on mines (company_id, name)
    op.create_unique_constraint('uq_mines_company_name', 'mines', ['company_id', 'name'])

    # 3. Add canonical MAC check constraint on gateways
    op.create_check_constraint(
        'ck_gateways_mac_canonical',
        'gateways',
        "mac ~ '^[0-9A-F]{2}(:[0-9A-F]{2}){5}$'",
    )

    # 4. Data repair on nodes: reset invalid placement states
    op.execute("""
        UPDATE nodes
        SET lat = NULL, lon = NULL, tunnel_id = NULL, placed_by = NULL, placed_at = NULL
        WHERE NOT (
            (tunnel_id IS NULL AND lat IS NULL AND lon IS NULL) OR
            (tunnel_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL)
        );
    """)
    op.execute("""
        UPDATE nodes
        SET lat = NULL, lon = NULL, tunnel_id = NULL, placed_by = NULL, placed_at = NULL
        FROM tunnels, gateways
        WHERE nodes.tunnel_id = tunnels.id
          AND nodes.gateway_id = gateways.id
          AND tunnels.mine_id <> gateways.mine_id;
    """)

    # 5. Add check constraint on nodes for complete placement
    op.create_check_constraint(
        'ck_nodes_placement_complete',
        'nodes',
        "(tunnel_id IS NULL AND lat IS NULL AND lon IS NULL) OR "
        "(tunnel_id IS NOT NULL AND lat IS NOT NULL AND lon IS NOT NULL)",
    )


def downgrade() -> None:
    # Drop constraints and tables in reverse order
    op.drop_constraint('ck_nodes_placement_complete', 'nodes', type_='check')
    op.drop_constraint('ck_gateways_mac_canonical', 'gateways', type_='check')
    op.drop_constraint('uq_mines_company_name', 'mines', type_='unique')
    op.drop_index(op.f('ix_audit_log_company_id'), table_name='audit_log')
    op.drop_index('ix_audit_log_company_id_ts', table_name='audit_log')
    op.drop_table('audit_log')
