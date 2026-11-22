"""step5_workers_assignments_shifts

Revision ID: c1506e641433
Revises: e252124b61df
Create Date: 2026-11-22 14:41:51.540249

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c1506e641433'
down_revision: Union[str, Sequence[str], None] = 'e252124b61df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # 1. worker_profiles table
    op.create_table(
        'worker_profiles',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('employee_no', sa.String(length=64), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=True),
        sa.Column('emergency_contact_name', sa.String(length=255), nullable=True),
        sa.Column('emergency_contact_phone', sa.String(length=32), nullable=True),
        sa.Column('joined_on', sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
        sa.UniqueConstraint('company_id', 'employee_no', name='uq_worker_profiles_company_employee_no')
    )
    op.create_index(op.f('ix_worker_profiles_company_id'), 'worker_profiles', ['company_id'], unique=False)

    # 2. assignments table
    op.create_table(
        'assignments',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('tunnel_id', sa.Integer(), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tunnel_id'], ['tunnels.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('ends_at > starts_at', name='ck_assignments_range'),
        postgresql.ExcludeConstraint(
            ('user_id', '='),
            (sa.func.tstzrange(sa.text('starts_at'), sa.text('ends_at'), '[)'), '&&'),
            using='gist',
            name='ex_assignments_user_no_overlap'
        )
    )
    op.create_index(op.f('ix_assignments_user_id'), 'assignments', ['user_id'], unique=False)
    op.create_index(op.f('ix_assignments_tunnel_id'), 'assignments', ['tunnel_id'], unique=False)
    op.create_index('ix_assignments_user_id_starts_at', 'assignments', ['user_id', sa.text('starts_at DESC')], unique=False)
    op.create_index('ix_assignments_tunnel_id_starts_at', 'assignments', ['tunnel_id', 'starts_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_assignments_tunnel_id_starts_at', table_name='assignments')
    op.drop_index('ix_assignments_user_id_starts_at', table_name='assignments')
    op.drop_index(op.f('ix_assignments_tunnel_id'), table_name='assignments')
    op.drop_index(op.f('ix_assignments_user_id'), table_name='assignments')
    op.drop_table('assignments')
    op.drop_index(op.f('ix_worker_profiles_company_id'), table_name='worker_profiles')
    op.drop_table('worker_profiles')
