"""step4_alerts_and_token_version

Revision ID: e252124b61df
Revises: e95f3b721a01
Create Date: 2026-11-22 12:08:36.651938

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e252124b61df'
down_revision: Union[str, Sequence[str], None] = 'e95f3b721a01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))

    op.create_table(
        'alerts',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('gateway_id', sa.Integer(), nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acknowledged_by', sa.Integer(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['gateway_id'], ['gateways.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint("kind IN ('node_offline', 'gateway_offline')", name='ck_alerts_kind'),
        sa.CheckConstraint(
            "(kind = 'node_offline' AND node_id IS NOT NULL) OR (kind = 'gateway_offline' AND node_id IS NULL)",
            name='ck_alerts_kind_node'
        )
    )
    op.create_index(op.f('ix_alerts_company_id'), 'alerts', ['company_id'], unique=False)
    op.create_index(op.f('ix_alerts_mine_id'), 'alerts', ['mine_id'], unique=False)
    op.create_index(op.f('ix_alerts_gateway_id'), 'alerts', ['gateway_id'], unique=False)
    op.create_index(op.f('ix_alerts_node_id'), 'alerts', ['node_id'], unique=False)
    op.create_index('ix_alerts_mine_id_opened_at', 'alerts', ['mine_id', sa.text('opened_at DESC')], unique=False)
    op.create_index(
        'uq_alerts_open_node',
        'alerts',
        ['node_id'],
        unique=True,
        postgresql_where=sa.text("kind = 'node_offline' AND resolved_at IS NULL")
    )
    op.create_index(
        'uq_alerts_open_gateway',
        'alerts',
        ['gateway_id'],
        unique=True,
        postgresql_where=sa.text("kind = 'gateway_offline' AND resolved_at IS NULL")
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'uq_alerts_open_gateway',
        table_name='alerts',
        postgresql_where=sa.text("kind = 'gateway_offline' AND resolved_at IS NULL")
    )
    op.drop_index(
        'uq_alerts_open_node',
        table_name='alerts',
        postgresql_where=sa.text("kind = 'node_offline' AND resolved_at IS NULL")
    )
    op.drop_index('ix_alerts_mine_id_opened_at', table_name='alerts')
    op.drop_index(op.f('ix_alerts_node_id'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_gateway_id'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_mine_id'), table_name='alerts')
    op.drop_index(op.f('ix_alerts_company_id'), table_name='alerts')
    op.drop_table('alerts')
    op.drop_column('users', 'token_version')
