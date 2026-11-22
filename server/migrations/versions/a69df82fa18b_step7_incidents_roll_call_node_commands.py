"""step7_incidents_roll_call_node_commands

Revision ID: a69df82fa18b
Revises: e2b8b74826ca
Create Date: 2026-11-23 00:20:04.236982

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a69df82fa18b'
down_revision: Union[str, Sequence[str], None] = 'e2b8b74826ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. incidents table
    op.create_table(
        'incidents',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('tunnel_id', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(length=8), nullable=False),
        sa.Column('level', sa.SmallInteger(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=8), server_default=sa.text("'open'"), nullable=False),
        sa.Column('siren_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('opened_by', sa.Integer(), nullable=True),
        sa.Column('opened_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('closed_by', sa.Integer(), nullable=True),
        sa.Column('close_note', sa.String(length=500), nullable=True),
        sa.CheckConstraint("source IN ('auto', 'manual', 'sos')", name='ck_incidents_source'),
        sa.CheckConstraint('level BETWEEN 1 AND 3', name='ck_incidents_level'),
        sa.CheckConstraint("status IN ('open', 'closed')", name='ck_incidents_status'),
        sa.CheckConstraint("((status = 'closed') = (closed_at IS NOT NULL))", name='ck_incidents_closed_status'),
        sa.ForeignKeyConstraint(['closed_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['opened_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tunnel_id'], ['tunnels.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_incidents_mine_id', 'incidents', ['mine_id'], unique=False)
    op.create_index('ix_incidents_tunnel_id', 'incidents', ['tunnel_id'], unique=False)
    op.create_index(
        'ix_incidents_mine_id_opened_at',
        'incidents',
        ['mine_id', sa.text('opened_at DESC')],
        unique=False,
    )
    op.create_index(
        'uq_incidents_open_auto',
        'incidents',
        ['tunnel_id'],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND source = 'auto'"),
    )

    # 2. incident_roster table
    op.create_table(
        'incident_roster',
        sa.Column('incident_id', sa.BigInteger(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('accounted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accounted_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['accounted_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('incident_id', 'user_id'),
    )
    op.create_index('ix_incident_roster_user_id', 'incident_roster', ['user_id'], unique=False)

    # 3. node_commands table
    op.create_table(
        'node_commands',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('node_id', sa.Integer(), nullable=False),
        sa.Column('cmd', sa.String(length=12), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('acked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cmd IN ('buzzer_on', 'buzzer_off', 'resume')", name='ck_node_commands_cmd'),
        sa.CheckConstraint('seq BETWEEN 1 AND 65535', name='ck_node_commands_seq'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['node_id'], ['nodes.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_node_commands_node_id', 'node_commands', ['node_id'], unique=False)
    op.create_index(
        'ix_node_commands_node_id_created_at',
        'node_commands',
        ['node_id', sa.text('created_at DESC')],
        unique=False,
    )
    op.create_index(
        'ix_node_commands_pending',
        'node_commands',
        ['node_id'],
        unique=False,
        postgresql_where=sa.text('acked_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_table('node_commands')
    op.drop_table('incident_roster')
    op.drop_table('incidents')
