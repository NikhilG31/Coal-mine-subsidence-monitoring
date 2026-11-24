"""step10_notifications

Revision ID: 382a31dc9a8e
Revises: 343347cf01f6
Create Date: 2026-11-24 22:44:58.807100

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '382a31dc9a8e'
down_revision: Union[str, Sequence[str], None] = '343347cf01f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. notification_settings table
    op.create_table(
        'notification_settings',
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('telegram_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('telegram_chat_id', sa.String(length=64), nullable=True),
        sa.Column('whatsapp_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('whatsapp_to', sa.String(length=20), nullable=True),
        sa.Column('webhook_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('webhook_url', sa.String(length=500), nullable=True),
        sa.Column('on_incident_open', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('on_incident_close', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('on_sos', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('on_offline', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(
            '(NOT telegram_enabled) OR (telegram_chat_id IS NOT NULL)',
            name='ck_notification_settings_telegram',
        ),
        sa.CheckConstraint(
            '(NOT whatsapp_enabled) OR (whatsapp_to IS NOT NULL)',
            name='ck_notification_settings_whatsapp',
        ),
        sa.CheckConstraint(
            '(NOT webhook_enabled) OR (webhook_url IS NOT NULL)',
            name='ck_notification_settings_webhook',
        ),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('mine_id'),
    )

    # 2. notification_log table
    op.create_table(
        'notification_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('mine_id', sa.Integer(), nullable=False),
        sa.Column('event', sa.String(length=20), nullable=False),
        sa.Column('ref_type', sa.String(length=16), nullable=True),
        sa.Column('ref_id', sa.BigInteger(), nullable=True),
        sa.Column('incident_id', sa.BigInteger(), nullable=True),
        sa.Column('channel', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('attempts', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('result', sa.String(length=200), nullable=True),
        sa.Column('message', sa.String(length=1000), nullable=False),
        sa.Column('dedupe_key', sa.String(length=120), nullable=False),
        sa.CheckConstraint(
            "event IN ('incident_open', 'incident_close', 'sos_report', 'offline_alert', 'test')",
            name='ck_notification_log_event',
        ),
        sa.CheckConstraint(
            "channel IN ('telegram', 'whatsapp', 'webhook')",
            name='ck_notification_log_channel',
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'skipped')",
            name='ck_notification_log_status',
        ),
        sa.CheckConstraint(
            "((status = 'sent' AND sent_at IS NOT NULL) OR (status <> 'sent' AND sent_at IS NULL))",
            name='ck_notification_log_sent_at',
        ),
        sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['mine_id'], ['mines.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedupe_key', 'channel', name='uq_notification_dedupe'),
    )
    op.create_index(
        'ix_notification_log_pending',
        'notification_log',
        ['status', 'next_attempt_at'],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        'ix_notification_log_mine_created',
        'notification_log',
        ['mine_id', sa.text('created_at DESC')],
        unique=False,
    )

    # 3. Drop redundant indexes from worker_reports
    op.drop_index('ix_worker_reports_mine_id', table_name='worker_reports')
    op.drop_index('ix_worker_reports_user_id', table_name='worker_reports')


def downgrade() -> None:
    # 3. Restore dropped indexes on worker_reports
    op.create_index('ix_worker_reports_user_id', 'worker_reports', ['user_id'], unique=False)
    op.create_index('ix_worker_reports_mine_id', 'worker_reports', ['mine_id'], unique=False)

    # 2. Drop notification_log indexes and table
    op.drop_index('ix_notification_log_mine_created', table_name='notification_log')
    op.drop_index('ix_notification_log_pending', table_name='notification_log')
    op.drop_table('notification_log')

    # 1. Drop notification_settings table
    op.drop_table('notification_settings')
