"""Shared by both jobs: who can be mailed, and the organization's look."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.users import User
from src.services.email.branding import OrgEmailBranding, resolve_org_email_branding
from src.services.nudges.links import org_base_url
from src.services.nudges.preferences import get_opted_out_user_ids

logger = logging.getLogger(__name__)


@dataclass
class Stats:
    emails: int = 0
    failed: int = 0
    considered: int = 0


@dataclass(frozen=True)
class OrgLook:
    org: Organization
    branding: OrgEmailBranding
    base_url: str


async def load_org_look(db: AsyncSession, org_id: int) -> OrgLook | None:
    org = await db.get(Organization, org_id)
    if org is None:
        return None
    org_config = (await db.execute(
        select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    )).scalars().first()
    return OrgLook(
        org=org,
        branding=resolve_org_email_branding(org, org_config, None),
        base_url=await org_base_url(org.slug, db, org.id),
    )


async def mailable_users(db: AsyncSession, user_ids: Iterable[int]) -> dict:
    """The users among ``user_ids`` that may be emailed, by id.

    Leaves out anyone without a verified address (content must not go to an
    address nobody proved they own) and anyone who unsubscribed from
    LearnHouse email or whose address bounced.
    """
    ids = set(user_ids)
    if not ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()  # type: ignore[union-attr]
    blocked = await get_opted_out_user_ids(db, ids)
    return {
        user.id: user
        for user in users
        if user.email and user.email_verified and user.id not in blocked
    }


def display_name(user: User | None) -> str:
    if user is None:
        return "Someone"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username
