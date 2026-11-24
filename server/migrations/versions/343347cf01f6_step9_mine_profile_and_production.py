"""step9_mine_profile_and_production

Revision ID: 343347cf01f6
Revises: 5d042c33b47c
Create Date: 2026-11-24 20:06:47.189484

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '343347cf01f6'
down_revision: Union[str, Sequence[str], None] = '5d042c33b47c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. mines: evacuation_notes text NULL with CHECK char_length <= 2000
    op.add_column('mines', sa.Column('evacuation_notes', sa.Text(), nullable=True))
    op.create_check_constraint(
        'ck_mines_evacuation_notes',
        'mines',
        'char_length(evacuation_notes) <= 2000',
    )

    # 2. mine_contacts table
    op.create_table(
        'mine_contacts',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('phone', sa.String(length=32), nullable=False),
        sa.Column('rank', sa.SmallInteger(), nullable=False),
        sa.CheckConstraint(
            "role IN ('shift_supervisor', 'safety_officer', 'rescue_station', 'other')",
            name='ck_mine_contacts_role',
        ),
        sa.CheckConstraint('rank BETWEEN 1 AND 20', name='ck_mine_contacts_rank'),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('mine_id', 'rank', name='uq_mine_contacts_rank'),
    )
    op.create_index('ix_mine_contacts_mine_id', 'mine_contacts', ['mine_id'], unique=False)

    # 3. mine_production table
    op.create_table(
        'mine_production',
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('planned_t', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('actual_t', sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('planned_t >= 0', name='ck_mine_production_planned_t'),
        sa.CheckConstraint('actual_t >= 0', name='ck_mine_production_actual_t'),
        sa.CheckConstraint(
            'planned_t IS NOT NULL OR actual_t IS NOT NULL',
            name='ck_mine_production_at_least_one',
        ),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('mine_id', 'day'),
    )


def downgrade() -> None:
    op.drop_table('mine_production')
    op.drop_index('ix_mine_contacts_mine_id', table_name='mine_contacts')
    op.drop_table('mine_contacts')
    op.drop_constraint('ck_mines_evacuation_notes', 'mines', type_='check')
    op.drop_column('mines', 'evacuation_notes')
