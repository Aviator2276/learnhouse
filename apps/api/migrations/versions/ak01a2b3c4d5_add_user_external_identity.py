"""Add userexternalidentity (Authentik / OpenID Connect account links)

Fork-only migration (branch label ``authentik``). Fork migrations form one
line after upstream's head; when an upstream release adds its own migrations,
``.fork/merge_migration_heads.py`` (run by the upstream sync) adds a merge
revision so the graph keeps the single head upstream's tests require.

Revision ID: ak01a2b3c4d5
Revises: f5a8b3d0c2e4
Create Date: 2026-11-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'ak01a2b3c4d5'
down_revision: Union[str, None] = 'f5a8b3d0c2e4'
branch_labels: Union[str, Sequence[str], None] = ('authentik',)
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'userexternalidentity',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('provider', sa.String(length=32), nullable=False),
        sa.Column('issuer', sa.String(length=512), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('email_at_link', sa.String(), nullable=False, server_default=''),
        sa.Column('creation_date', sa.String(), nullable=False, server_default=''),
        sa.Column('last_login_at', sa.String(), nullable=False, server_default=''),
        sa.UniqueConstraint('provider', 'issuer', 'subject', name='uq_user_ext_identity_subject'),
        sa.UniqueConstraint('user_id', 'provider', 'issuer', name='uq_user_ext_identity_user'),
    )
    op.create_index('ix_user_ext_identity_user', 'userexternalidentity', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_user_ext_identity_user', table_name='userexternalidentity')
    op.drop_table('userexternalidentity')
