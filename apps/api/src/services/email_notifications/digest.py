"""The weekly digest: each organization's most active discussions.

Checked every hour. An organization is due on its chosen weekday once the
chosen hour has passed in its time zone. ``last_digest_at`` is written
before sending starts, so a restart or a second replica cannot send the same
week twice.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.communities.communities import Community
from src.db.communities.discussion_comments import DiscussionComment
from src.db.communities.discussions import Discussion
from src.db.email_notifications import EmailNotificationSettings
from src.db.users import User
from src.services.email_notifications import emails
from src.services.email_notifications.audience import OrgAudience, load_org_audience
from src.services.email_notifications.delivery import (
    Stats,
    display_name,
    load_org_look,
    mailable_users,
)
from src.services.email_notifications.rules import parse_created, plain_text, wants_digest

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
# Two digests are never closer than this, whatever an admin changes.
MIN_GAP = timedelta(hours=20)
MAX_ENTRIES = 5
MAX_ROWS = 5000
# A comment says more about a discussion being alive than an upvote does.
COMMENT_WEIGHT = 2


@dataclass
class Thread:
    discussion: Discussion
    community: Community
    is_new: bool
    new_comments: int

    @property
    def score(self) -> int:
        return (self.discussion.upvote_count or 0) + COMMENT_WEIGHT * self.new_comments


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_due(settings: EmailNotificationSettings, now: datetime) -> bool:
    """``now`` is timezone-aware."""
    if not settings.enabled or not settings.digest_enabled:
        return False
    local = now.astimezone(zone(settings.timezone))
    if local.weekday() != settings.digest_weekday or local.hour < settings.digest_hour:
        return False
    last = settings.last_digest_at
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= MIN_GAP


async def week_threads(db: AsyncSession, org_id: int, local_now: datetime) -> list:
    """Discussions of ``org_id`` that were posted or commented on in the
    window. ``local_now`` is naive local time, like the rows' own dates."""
    since = local_now - WINDOW
    communities = {c.id: c for c in (await db.execute(
        select(Community).where(Community.org_id == org_id)
    )).scalars().all()}
    if not communities:
        return []

    recent = [d for d in (await db.execute(
        select(Discussion).where(Discussion.org_id == org_id).order_by(Discussion.id.desc()).limit(MAX_ROWS)  # type: ignore[union-attr]
    )).scalars().all() if (parse_created(d.creation_date) or datetime.min) >= since]
    threads = {
        d.id: Thread(d, communities[d.community_id], True, 0) for d in recent if d.community_id in communities
    }

    comment_rows = (await db.execute(
        select(DiscussionComment, Discussion)
        .join(Discussion, Discussion.id == DiscussionComment.discussion_id)  # type: ignore[arg-type]
        .where(Discussion.org_id == org_id)
        .order_by(DiscussionComment.id.desc())  # type: ignore[union-attr]
        .limit(MAX_ROWS)
    )).all()
    for comment, discussion in comment_rows:
        if (parse_created(comment.creation_date) or datetime.min) < since:
            continue
        if discussion.community_id not in communities:
            continue
        thread = threads.setdefault(
            discussion.id, Thread(discussion, communities[discussion.community_id], False, 0)
        )
        thread.new_comments += 1
    return list(threads.values())


def pick(threads: list, audience_pref, readable: set) -> list:
    """The member's share of the week, most active first."""
    mine = [
        t for t in threads
        if t.community.id in readable
        and t.community.id not in audience_pref.muted_communities
        and (t.discussion.label or "general") not in audience_pref.muted_labels
    ]
    mine.sort(key=lambda t: (t.discussion.is_pinned, t.score, t.discussion.id), reverse=True)
    return mine


async def build_for_member(
    db: AsyncSession, audience: OrgAudience, threads: list, user_id: int, authors: dict
) -> Optional[tuple]:
    """``(entries, new_posts, new_comments)`` for one member, or None when
    the week holds nothing they can read and have not muted."""
    readable = set()
    for community in {t.community.id: t.community for t in threads}.values():
        if user_id in await audience.readers(db, community):
            readable.add(community.id)
    mine = pick(threads, audience.preference(user_id), readable)
    if not mine:
        return None
    entries = [
        emails.Entry(
            reason="digest",
            community_uuid=t.community.community_uuid,
            community_name=t.community.name,
            discussion_uuid=t.discussion.discussion_uuid,
            title=t.discussion.title,
            author=display_name(authors.get(t.discussion.author_id)),
            snippet=plain_text(t.discussion.content) or t.community.name,
            emoji=t.discussion.emoji or "",
            count=t.new_comments,
        )
        for t in mine[:MAX_ENTRIES]
    ]
    return entries, sum(1 for t in mine if t.is_new), sum(t.new_comments for t in mine)


async def load_authors(db: AsyncSession, threads: list) -> dict:
    ids = {t.discussion.author_id for t in threads}
    if not ids:
        return {}
    return {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()}  # type: ignore[union-attr]


async def send_org_digest(db: AsyncSession, org_id: int, local_now: datetime, stats: Stats) -> None:
    threads = await week_threads(db, org_id, local_now)
    if not threads:
        return
    audience = await load_org_audience(db, org_id)
    wanting = [uid for uid in audience.members if wants_digest(audience.preference(uid))]
    recipients = await mailable_users(db, wanting)
    if not recipients:
        return
    look = await load_org_look(db, org_id)
    if look is None:
        return
    authors = await load_authors(db, threads)
    for user_id, user in recipients.items():
        stats.considered += 1
        try:
            built = await build_for_member(db, audience, threads, user_id, authors)
            if built is None:
                continue
            entries, new_posts, new_comments = built
            sent = await asyncio.to_thread(
                emails.send_digest_email,
                email=user.email,
                username=user.username,
                org_name=look.branding.org_name,
                entries=entries,
                new_posts=new_posts,
                new_comments=new_comments,
                base_url=look.base_url,
                one_click_url=emails.unsubscribe_url(look.base_url, user.user_uuid, org_id),
                **look.branding.as_kwargs(),
            )
            if sent is False:
                stats.failed += 1
            else:
                stats.emails += 1
        except Exception:
            stats.failed += 1
            logger.warning("Community digest failed for user %s", user_id, exc_info=True)


async def _claim(db: AsyncSession, settings: EmailNotificationSettings, now: datetime) -> bool:
    """Write ``last_digest_at``; False when another replica got there first."""
    if settings.id is None:
        settings.last_digest_at = now
        db.add(settings)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return False
        return True
    previous = settings.last_digest_at
    unchanged = (
        EmailNotificationSettings.last_digest_at.is_(None)  # type: ignore[union-attr]
        if previous is None
        else EmailNotificationSettings.last_digest_at == previous
    )
    claimed = await db.execute(
        update(EmailNotificationSettings)
        .where(EmailNotificationSettings.id == settings.id, unchanged)  # type: ignore[arg-type]
        .values(last_digest_at=now)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return claimed.rowcount == 1


async def run_digests(db: AsyncSession, now: Optional[datetime] = None) -> Stats:
    stats = Stats()
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone().replace(tzinfo=None)

    org_ids = (await db.execute(select(Community.org_id).distinct())).scalars().all()
    for org_id in org_ids:
        settings = (await db.execute(
            select(EmailNotificationSettings).where(EmailNotificationSettings.org_id == org_id)
        )).scalars().first()
        if settings is None:
            settings = EmailNotificationSettings(org_id=org_id)
        if not is_due(settings, now):
            continue
        # Claim the week first. A crash after this skips one digest; the
        # other order could send it twice.
        if not await _claim(db, settings, now):
            continue
        try:
            await send_org_digest(db, org_id, local_now, stats)
        except Exception:
            logger.exception("Community digest run failed for organization %s", org_id)
    return stats
