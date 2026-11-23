"""step8_worker_reports_and_sos

Revision ID: 5d042c33b47c
Revises: a69df82fa18b
Create Date: 2026-11-23 22:38:56.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d042c33b47c'
down_revision: Union[str, Sequence[str], None] = 'a69df82fa18b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. worker_reports table
    op.create_table(
        'worker_reports',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('tunnel_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('node_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=6), nullable=False),
        sa.Column('type', sa.String(length=16), nullable=False),
        sa.Column('severity', sa.String(length=8), nullable=False),
        sa.Column('description', sa.String(length=1000), nullable=True),
        sa.Column('status', sa.String(length=12), server_default=sa.text("'open'"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_signal_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('repeat_count', sa.Integer(), server_default=sa.text('1'), nullable=False),
        sa.Column('acknowledged_by', sa.Integer(), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by', sa.Integer(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution_note', sa.String(length=500), nullable=True),
        sa.Column('incident_id', sa.BigInteger(), nullable=True),
        sa.CheckConstraint("source IN ('worker', 'device')", name='ck_reports_source'),
        sa.CheckConstraint("type IN ('injury', 'equipment_fault', 'gas_smell', 'need_help', 'sos')", name='ck_reports_type'),
        sa.CheckConstraint("severity IN ('low', 'medium', 'high', 'critical')", name='ck_reports_severity'),
        sa.CheckConstraint("status IN ('open', 'acknowledged', 'resolved')", name='ck_reports_status'),
        sa.CheckConstraint('repeat_count >= 1', name='ck_reports_repeat_count'),
        sa.CheckConstraint("(source = 'device' AND node_id IS NOT NULL AND user_id IS NULL AND type = 'sos') OR (source = 'worker')", name='ck_reports_source_shape'),
        sa.CheckConstraint(
            "(((status = 'open') AND (acknowledged_at IS NULL) AND (resolved_at IS NULL)) OR "
            "((status = 'acknowledged') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NULL)) OR "
            "((status = 'resolved') AND (acknowledged_at IS NOT NULL) AND (resolved_at IS NOT NULL)))",
            name='ck_reports_status_fields',
        ),
        sa.CheckConstraint("(type <> 'sos' OR severity = 'critical')", name='ck_reports_sos_critical'),
        sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tunnel_id'], ['tunnels.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_worker_reports_mine_id', 'worker_reports', ['mine_id'], unique=False)
    op.create_index('ix_worker_reports_tunnel_id', 'worker_reports', ['tunnel_id'], unique=False)
    op.create_index('ix_worker_reports_user_id', 'worker_reports', ['user_id'], unique=False)
    op.create_index('ix_worker_reports_node_id', 'worker_reports', ['node_id'], unique=False)
    op.create_index('ix_worker_reports_incident_id', 'worker_reports', ['incident_id'], unique=False)
    op.create_index(
        'ix_worker_reports_mine_status_created',
        'worker_reports',
        ['mine_id', 'status', sa.text('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'ix_worker_reports_user_created',
        'worker_reports',
        ['user_id', sa.text('created_at DESC')],
        unique=False,
        postgresql_where=sa.text('user_id IS NOT NULL'),
    )
    op.create_index(
        'ix_worker_reports_node_signal',
        'worker_reports',
        ['node_id', sa.text('last_signal_at DESC')],
        unique=False,
        postgresql_where=sa.text("source = 'device'"),
    )

    # 2. incidents partial unique index for SOS
    op.create_index(
        'uq_incidents_open_sos',
        'incidents',
        ['tunnel_id'],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND source = 'sos'"),
    )


def downgrade() -> None:
    op.drop_index('uq_incidents_open_sos', table_name='incidents')
    op.drop_table('worker_reports')
