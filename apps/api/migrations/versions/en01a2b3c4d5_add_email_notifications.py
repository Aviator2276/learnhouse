"""Add email notification tables (communities and assignments)

Fork-only migration, chained after the fork's plugNmeet one so the graph
keeps a single head. See ``.fork/merge_migration_heads.py`` for what happens
when an upstream release adds migrations of its own.

Revision ID: en01a2b3c4d5
Revises: pn01a2b3c4d5
Create Date: 2026-11-16 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'en01a2b3c4d5'
down_revision: Union[str, None] = 'pn01a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'emailnotificationsettings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('default_level', sa.String(length=20), nullable=False, server_default='highlights'),
        sa.Column('digest_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('digest_weekday', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('digest_hour', sa.Integer(), nullable=False, server_default='8'),
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='UTC'),
        sa.Column('assignments_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('assignment_reminder_days', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('last_digest_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('org_id', name='uq_emailnotificationsettings_org'),
    )
    op.create_table(
        'emailnotificationpreference',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('level', sa.String(length=20), nullable=True),
        sa.Column('weekly_digest', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('muted_community_ids', sa.JSON(), nullable=False),
        sa.Column('muted_labels', sa.JSON(), nullable=False),
        sa.Column('assignment_updates', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('assignment_reminders', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('assignment_reminder_days', sa.Integer(), nullable=True),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('user_id', 'org_id', name='uq_emailnotificationpreference_user_org'),
    )
    op.create_index(
        'ix_emailnotificationpreference_user_id', 'emailnotificationpreference', ['user_id']
    )
    op.create_table(
        'emailnotificationcursor',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('last_discussion_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_comment_id', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('assignments_seeded', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        'emailnotificationassignmentstate',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('assignment_id', sa.Integer(), sa.ForeignKey('assignment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('visible', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('due_date', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('assignment_id', name='uq_emailnotificationassignmentstate_assignment'),
    )
    op.create_table(
        'emailnotificationreminder',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('assignment_id', sa.Integer(), sa.ForeignKey('assignment.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('due_date', sa.String(), nullable=False),
        sa.UniqueConstraint('assignment_id', 'user_id', 'due_date', name='uq_emailnotificationreminder'),
    )


def downgrade() -> None:
    op.drop_table('emailnotificationreminder')
    op.drop_table('emailnotificationassignmentstate')
    op.drop_table('emailnotificationcursor')
    op.drop_index('ix_emailnotificationpreference_user_id', table_name='emailnotificationpreference')
    op.drop_table('emailnotificationpreference')
    op.drop_table('emailnotificationsettings')
