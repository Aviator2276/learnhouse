"""Add liveclass and liveclassattendance (plugNmeet live classes)

Fork-only migration, chained after the fork's Authentik one so the graph
keeps a single head (upstream's test_migration_graph requires it). When an
upstream release adds migrations, ``.fork/merge_migration_heads.py`` (run by
the upstream sync) adds a merge revision that joins both lines again.

Revision ID: pn01a2b3c4d5
Revises: ak01a2b3c4d5
Create Date: 2026-11-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'pn01a2b3c4d5'
down_revision: Union[str, None] = 'ak01a2b3c4d5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'liveclass',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('class_uuid', sa.String(length=64), nullable=False, unique=True),
        sa.Column('org_id', sa.Integer(), sa.ForeignKey('organization.id', ondelete='CASCADE'), nullable=False),
        sa.Column('course_id', sa.Integer(), sa.ForeignKey('course.id', ondelete='CASCADE'), nullable=True),
        sa.Column('series_uuid', sa.String(length=64), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.String(length=2000), nullable=False, server_default=''),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False, server_default='UTC'),
        sa.Column('cancelled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.Column('update_date', sa.String(), nullable=False, server_default=''),
    )
    op.create_index('ix_liveclass_org_starts', 'liveclass', ['org_id', 'starts_at'])
    op.create_index('ix_liveclass_org_ends', 'liveclass', ['org_id', 'ends_at'])
    op.create_index('ix_liveclass_course', 'liveclass', ['course_id'])
    op.create_index('ix_liveclass_series', 'liveclass', ['series_uuid'])

    op.create_table(
        'liveclassattendance',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('class_id', sa.Integer(), sa.ForeignKey('liveclass.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_liveclassattendance_class_user', 'liveclassattendance', ['class_id', 'user_id'])


def downgrade() -> None:
    op.drop_index('ix_liveclassattendance_class_user', table_name='liveclassattendance')
    op.drop_table('liveclassattendance')
    op.drop_index('ix_liveclass_series', table_name='liveclass')
    op.drop_index('ix_liveclass_course', table_name='liveclass')
    op.drop_index('ix_liveclass_org_ends', table_name='liveclass')
    op.drop_index('ix_liveclass_org_starts', table_name='liveclass')
    op.drop_table('liveclass')
