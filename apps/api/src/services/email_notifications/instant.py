"""Email members about new posts and comments.

Every five minutes: read the posts and comments added since the last run
that are old enough to send, work out who wants each one, and send one email
per member and organization. The cursor moves before anything is sent, so a
mail provider failure can never cause the same post to be emailed twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, update
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.communities.communities import Community
from src.db.communities.discussion_comments import DiscussionComment
from src.db.communities.discussions import Discussion
from src.db.email_notifications import EmailNotificationCursor
from src.db.users import User
from src.services.email_notifications import emails
from src.services.email_notifications.audience import OrgAudience, load_org_audience
from src.services.email_notifications.config import get_config
from src.services.email_notifications.delivery import (
    Stats,
    display_name,
    load_org_look,
    mailable_users,
)
from src.services.email_notifications.rules import (
    REASON_MENTION,
    REASON_NEW,
    REASON_RANK,
    REASON_REPLY,
    REASON_STAFF,
    mentioned_usernames,
    parse_created,
    plain_text,
    wants,
)

logger = logging.getLogger(__name__)

# A post older than this when the job gets to it is skipped: the job was off
# for a while, and last week's posts are not news.
LOOKBACK = timedelta(days=2)
MAX_ROWS = 2000
CURSOR_ID = 1


def _ready(rows: list, now: datetime, delay: timedelta) -> list:
    """The leading rows that are old enough to send, in id order.

    Stops at the first row still inside the delay so the cursor never jumps
    over a post it has not sent.
    """
    ready = []
    for row in rows:
        created = parse_created(row.creation_date)
        if created is not None and created > now - delay:
            break
        ready.append(row)
    return ready


def _fresh(row, now: datetime) -> bool:
    created = parse_created(row.creation_date)
    return created is not None and created >= now - LOOKBACK


async def _start_cursor(db: AsyncSession) -> None:
    """First run: begin at the newest post, so installing the feature does
    not email everyone about posts that are already there."""
    last_discussion = (await db.execute(select(func.max(Discussion.id)))).scalar() or 0
    last_comment = (await db.execute(select(func.max(DiscussionComment.id)))).scalar() or 0
    db.add(EmailNotificationCursor(
        id=CURSOR_ID, last_discussion_id=last_discussion, last_comment_id=last_comment
    ))
    await db.commit()


def _add(found: dict, user_id: int, org_id: int, entry: emails.Entry) -> None:
    """Keep one entry per discussion: the strongest reason, the newest text."""
    per_user = found.setdefault((user_id, org_id), {})
    previous = per_user.get(entry.discussion_uuid)
    if previous is not None:
        entry.count = previous.count + 1
        if REASON_RANK[previous.reason] < REASON_RANK[entry.reason]:
            entry.reason = previous.reason
    per_user[entry.discussion_uuid] = entry


async def run_instant(db: AsyncSession, now: Optional[datetime] = None) -> Stats:
    """``now`` is naive local time, the clock upstream stamps posts with."""
    stats = Stats()
    now = now or datetime.now()
    delay = timedelta(minutes=get_config().delay_minutes)

    cursor = await db.get(EmailNotificationCursor, CURSOR_ID)
    if cursor is None:
        await _start_cursor(db)
        return stats

    discussions = _ready(list((await db.execute(
        select(Discussion).where(Discussion.id > cursor.last_discussion_id).order_by(Discussion.id).limit(MAX_ROWS)  # type: ignore[arg-type]
    )).scalars().all()), now, delay)
    comments = _ready(list((await db.execute(
        select(DiscussionComment).where(DiscussionComment.id > cursor.last_comment_id)
        .order_by(DiscussionComment.id).limit(MAX_ROWS)  # type: ignore[arg-type]
    )).scalars().all()), now, delay)
    if not discussions and not comments:
        return stats

    # Move the cursor only if nobody else has: two replicas reading the same
    # rows must not both send them.
    claimed = await db.execute(
        update(EmailNotificationCursor)
        .where(
            EmailNotificationCursor.id == CURSOR_ID,  # type: ignore[arg-type]
            EmailNotificationCursor.last_discussion_id == cursor.last_discussion_id,  # type: ignore[arg-type]
            EmailNotificationCursor.last_comment_id == cursor.last_comment_id,  # type: ignore[arg-type]
        )
        .values(
            last_discussion_id=discussions[-1].id if discussions else cursor.last_discussion_id,
            last_comment_id=comments[-1].id if comments else cursor.last_comment_id,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if claimed.rowcount != 1:
        return stats

    discussions = [d for d in discussions if _fresh(d, now)]
    comments = [c for c in comments if _fresh(c, now)]
    stats.considered = len(discussions) + len(comments)
    if not stats.considered:
        return stats

    # Everything the new rows point at, loaded once.
    threads = {d.id: d for d in discussions}
    missing = {c.discussion_id for c in comments} - set(threads)
    if missing:
        threads.update({d.id: d for d in (await db.execute(
            select(Discussion).where(Discussion.id.in_(missing))  # type: ignore[union-attr]
        )).scalars().all()})
    communities = {c.id: c for c in (await db.execute(
        select(Community).where(Community.id.in_({d.community_id for d in threads.values()}))  # type: ignore[union-attr]
    )).scalars().all()}
    author_ids = {d.author_id for d in discussions} | {c.author_id for c in comments}
    authors = {u.id: u for u in (await db.execute(
        select(User).where(User.id.in_(author_ids))  # type: ignore[union-attr]
    )).scalars().all()}
    # Who was already in each thread, by comment id, to tell a reply from news.
    earlier: dict = {}
    if comments:
        for discussion_id, comment_id, author_id in (await db.execute(
            select(DiscussionComment.discussion_id, DiscussionComment.id, DiscussionComment.author_id)
            .where(DiscussionComment.discussion_id.in_({c.discussion_id for c in comments}))  # type: ignore[attr-defined]
        )).all():
            earlier.setdefault(discussion_id, []).append((comment_id, author_id))

    audiences: dict = {}
    usernames: dict = {}

    async def audience_for(org_id: int) -> OrgAudience:
        if org_id not in audiences:
            audiences[org_id] = await load_org_audience(db, org_id)
            members = (await db.execute(
                select(User.id, User.username).where(User.id.in_(set(audiences[org_id].members)))  # type: ignore[union-attr]
            )).all() if audiences[org_id].members else []
            usernames[org_id] = {(name or "").lower(): uid for uid, name in members}
        return audiences[org_id]

    found: dict = {}

    async def consider(discussion: Discussion, author_id: int, text: str, comment: Optional[DiscussionComment]) -> None:
        community = communities.get(discussion.community_id)
        if community is None:
            return
        audience = await audience_for(community.org_id)
        if not audience.settings.enabled:
            return
        readers = await audience.readers(db, community)
        names = usernames[community.org_id]
        mentioned = {names[name] for name in mentioned_usernames(text) if name in names}
        if comment is None:
            involved: set = set()
            by_staff = author_id in await audience.instructors(db, community)
        else:
            involved = {discussion.author_id} | {
                uid for cid, uid in earlier.get(discussion.id, []) if cid < comment.id
            }
            by_staff = False
        snippet = plain_text(comment.content if comment is not None else discussion.content)
        for user_id in readers:
            if user_id == author_id:
                continue
            if user_id in mentioned:
                reason = REASON_MENTION
            elif user_id in involved:
                reason = REASON_REPLY
            elif by_staff:
                reason = REASON_STAFF
            else:
                reason = REASON_NEW
            if not wants(audience.preference(user_id), reason, community.id, discussion.label):
                continue
            _add(found, user_id, community.org_id, emails.Entry(
                reason=reason,
                community_uuid=community.community_uuid,
                community_name=community.name,
                discussion_uuid=discussion.discussion_uuid,
                title=discussion.title,
                author=display_name(authors.get(author_id)),
                snippet=snippet or discussion.title,
                is_comment=comment is not None,
                emoji=discussion.emoji or "",
            ))

    for discussion in discussions:
        await consider(discussion, discussion.author_id, f"{discussion.title} {plain_text(discussion.content)}", None)
    for comment in comments:
        thread = threads.get(comment.discussion_id)
        if thread is not None:
            await consider(thread, comment.author_id, plain_text(comment.content), comment)

    if not found:
        return stats

    recipients = await mailable_users(db, {user_id for user_id, _ in found})
    looks: dict = {}
    for (user_id, org_id), per_discussion in found.items():
        user = recipients.get(user_id)
        if user is None:
            continue
        try:
            if org_id not in looks:
                looks[org_id] = await load_org_look(db, org_id)
            look = looks[org_id]
            if look is None:
                continue
            # Newest activity first: entries were added in posting order.
            entries = list(per_discussion.values())[::-1]
            sent = await asyncio.to_thread(
                emails.send_instant_email,
                email=user.email,
                username=user.username,
                org_name=look.branding.org_name,
                entries=entries,
                base_url=look.base_url,
                one_click_url=emails.unsubscribe_url(look.base_url, user.user_uuid, org_id),
                **look.branding.as_kwargs(),
            )
            # The sender logs and returns False when the provider refuses.
            if sent is False:
                stats.failed += 1
            else:
                stats.emails += 1
        except Exception:
            stats.failed += 1
            logger.warning("Community email failed for user %s", user_id, exc_info=True)
    return stats
