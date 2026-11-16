"""Read and write a member's choices and an organization's settings."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.communities.communities import Community
from src.db.email_notifications import EmailNotificationPreference, EmailNotificationSettings
from src.services.email_notifications.audience import OrgAudience
from src.services.email_notifications.rules import LABEL_IDS, LEVEL_OFF


async def readable_communities(db: AsyncSession, audience: OrgAudience, user_id: int) -> list:
    """The organization's communities this member may read, by name."""
    communities = (await db.execute(
        select(Community).where(Community.org_id == audience.org_id).order_by(Community.name)
    )).scalars().all()
    return [c for c in communities if user_id in await audience.readers(db, c)]


async def get_preference_row(
    db: AsyncSession, user_id: int, org_id: int
) -> Optional[EmailNotificationPreference]:
    return (await db.execute(
        select(EmailNotificationPreference).where(
            EmailNotificationPreference.user_id == user_id,
            EmailNotificationPreference.org_id == org_id,
        )
    )).scalars().first()


async def save_preference(
    db: AsyncSession,
    user_id: int,
    org_id: int,
    *,
    level: str,
    weekly_digest: bool,
    muted_community_ids: list,
    muted_labels: list,
    assignment_updates: bool,
    assignment_reminders: bool,
    assignment_reminder_days: Optional[int],
) -> EmailNotificationPreference:
    row = await get_preference_row(db, user_id, org_id)
    if row is None:
        row = EmailNotificationPreference(user_id=user_id, org_id=org_id)
    row.level = level
    row.weekly_digest = weekly_digest
    row.muted_community_ids = sorted(set(muted_community_ids))
    row.muted_labels = [label for label in LABEL_IDS if label in set(muted_labels)]
    row.assignment_updates = assignment_updates
    row.assignment_reminders = assignment_reminders
    row.assignment_reminder_days = assignment_reminder_days
    row.update_date = str(datetime.now())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def turn_off(db: AsyncSession, user_id: int, org_id: int) -> None:
    """Stop every notification email for this member: communities, the
    digest and assignments. Idempotent, and keeps the member's community and
    post type choices so turning email back on restores them."""
    row = await get_preference_row(db, user_id, org_id)
    if row is None:
        row = EmailNotificationPreference(user_id=user_id, org_id=org_id)
    row.level = LEVEL_OFF
    row.assignment_updates = False
    row.assignment_reminders = False
    row.update_date = str(datetime.now())
    db.add(row)
    await db.commit()


async def save_settings(
    db: AsyncSession,
    org_id: int,
    *,
    enabled: bool,
    default_level: str,
    digest_enabled: bool,
    digest_weekday: int,
    digest_hour: int,
    timezone: str,
    assignments_enabled: bool,
    assignment_reminder_days: int,
) -> EmailNotificationSettings:
    row = (await db.execute(
        select(EmailNotificationSettings).where(EmailNotificationSettings.org_id == org_id)
    )).scalars().first()
    if row is None:
        row = EmailNotificationSettings(org_id=org_id)
    row.enabled = enabled
    row.default_level = default_level
    row.digest_enabled = digest_enabled
    row.digest_weekday = digest_weekday
    row.digest_hour = digest_hour
    row.timezone = timezone
    row.assignments_enabled = assignments_enabled
    row.assignment_reminder_days = assignment_reminder_days
    row.update_date = str(datetime.now())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
