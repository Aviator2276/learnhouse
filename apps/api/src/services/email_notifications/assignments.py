"""Email learners about assignments: a new one, a due date that was set or
moved, and a reminder before the due date.

Upstream's assignment code is not changed. Every five minutes the job
compares each assignment with what it saw last time
(``EmailNotificationAssignmentState``); a difference is the event. The saved
state is updated before any email goes out, and each reminder is claimed in
``EmailNotificationReminder`` first, so nothing is sent twice.

Only learners enrolled in the course (a trail run) who are still members of
the organization and can still read the course are emailed, and only about
an assignment that is published inside a published activity.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.assignments import (
    Assignment,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
)
from src.db.courses.courses import Course
from src.db.email_notifications import (
    EmailNotificationAssignmentState,
    EmailNotificationCursor,
    EmailNotificationReminder,
)
from src.db.trail_runs import TrailRun
from src.db.users import PublicUser, User
from src.security.rbac import AccessAction, check_resource_access
from src.services.calendar.sources.assignments import parse_due_date
from src.services.email_notifications import emails
from src.services.email_notifications.audience import OrgAudience, load_org_audience
from src.services.email_notifications.delivery import Stats, load_org_look, mailable_users
from src.services.email_notifications.digest import zone
from src.services.email_notifications.instant import CURSOR_ID, _start_cursor
from src.services.email_notifications.rules import REMINDER_DAY_CHOICES

logger = logging.getLogger(__name__)

MAX_LEAD = timedelta(days=max(REMINDER_DAY_CHOICES))
_DONE = (
    AssignmentUserSubmissionStatus.SUBMITTED,
    AssignmentUserSubmissionStatus.GRADED,
    AssignmentUserSubmissionStatus.LATE,
)


@dataclass(frozen=True)
class Item:
    """An assignment as plain values. Rows are copied on load because a lost
    claim rolls the session back, which would leave ORM objects unreadable."""

    assignment_id: int
    title: str
    due_raw: str
    # Published, in a published activity: learners can see it.
    visible: bool
    org_id: int
    course_id: int
    course_uuid: str
    course_name: str
    activity_uuid: str


def due_moment(raw: str) -> Optional[datetime]:
    """When the deadline passes, as aware UTC. A date without a time lasts
    the whole day, like upstream's own late check."""
    value, all_day = parse_due_date(raw)
    if value is None:
        return None
    if all_day:
        assert isinstance(value, date)
        return datetime.combine(value + timedelta(days=1), time.min, tzinfo=timezone.utc)
    assert isinstance(value, datetime)
    return value


def reminder_anchor(raw: str) -> Optional[datetime]:
    """The moment reminders count back from: the due time, or the start of
    the due day for a date without a time, so "one day before" a Friday
    deadline is Thursday and not Friday."""
    value, all_day = parse_due_date(raw)
    if value is None:
        return None
    if all_day:
        assert isinstance(value, date)
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    assert isinstance(value, datetime)
    return value


def due_text(raw: str, tz_name: str) -> str:
    """The due date the way a person reads it, in the organization's zone."""
    value, all_day = parse_due_date(raw)
    if value is None:
        return ""
    if all_day:
        return f"{value:%a, %b} {value.day}"
    local = value.astimezone(zone(tz_name))  # type: ignore[union-attr]
    return f"{local:%a, %b} {local.day} at {local:%H:%M} ({local:%Z})"


def activity_url(base: str, item: Item) -> str:
    return (
        f"{base}/course/{item.course_uuid.removeprefix('course_')}"
        f"/activity/{item.activity_uuid.removeprefix('activity_')}"
    )


async def _load_items(db: AsyncSession) -> list:
    rows = (await db.execute(
        select(Assignment, Activity, Course)
        .join(Activity, Activity.id == Assignment.activity_id)  # type: ignore[arg-type]
        .join(Course, Course.id == Assignment.course_id)  # type: ignore[arg-type]
    )).all()
    return [
        Item(
            assignment_id=assignment.id,
            title=assignment.title or "Assignment",
            due_raw=(assignment.due_date or "").strip(),
            visible=bool(assignment.published) and bool(activity.published),
            org_id=course.org_id,
            course_id=course.id,
            course_uuid=course.course_uuid,
            course_name=course.name,
            activity_uuid=activity.activity_uuid,
        )
        for assignment, activity, course in rows
    ]


async def _seed(db: AsyncSession, cursor: Optional[EmailNotificationCursor], items: list) -> None:
    """First run: remember every assignment as it is and send nothing, so
    installing the feature does not announce old assignments."""
    for item in items:
        db.add(EmailNotificationAssignmentState(
            assignment_id=item.assignment_id, visible=item.visible, due_date=item.due_raw
        ))
    if cursor is None:
        # Start the post cursor at the newest post too, not at zero.
        await _start_cursor(db)
        cursor = await db.get(EmailNotificationCursor, CURSOR_ID)
        assert cursor is not None
    cursor.assignments_seeded = True
    db.add(cursor)
    await db.commit()


async def _record(
    db: AsyncSession, item: Item, previous: Optional[EmailNotificationAssignmentState]
) -> bool:
    """Save what the assignment looks like now. False when another replica
    saved it first, in which case that replica sends the email."""
    if previous is None:
        db.add(EmailNotificationAssignmentState(
            assignment_id=item.assignment_id, visible=item.visible, due_date=item.due_raw
        ))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return False
        return True
    claimed = await db.execute(
        update(EmailNotificationAssignmentState)
        .where(
            EmailNotificationAssignmentState.id == previous.id,  # type: ignore[arg-type]
            EmailNotificationAssignmentState.visible == previous.visible,  # type: ignore[arg-type]
            EmailNotificationAssignmentState.due_date == previous.due_date,  # type: ignore[arg-type]
        )
        .values(visible=item.visible, due_date=item.due_raw)
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return claimed.rowcount == 1


def _change(item: Item, previous: Optional[EmailNotificationAssignmentState], now: datetime) -> Optional[str]:
    """What happened to the assignment since the last run that a learner
    should hear about, if anything."""
    if not item.visible:
        return None
    due = due_moment(item.due_raw)
    if due is not None and due <= now:
        return None
    if previous is None or not previous.visible:
        return emails.KIND_NEW
    if previous.due_date != item.due_raw and due is not None:
        return emails.KIND_DUE
    return None


async def _claim_reminder(db: AsyncSession, assignment_id: int, user_id: int, due_raw: str) -> bool:
    db.add(EmailNotificationReminder(assignment_id=assignment_id, user_id=user_id, due_date=due_raw))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


class Readers:
    """Enrolled learners of a course who can still read it, checked with
    LearnHouse's own course access rule and remembered for the run."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._enrolled: dict = {}
        self._allowed: dict = {}
        self._users: dict = {}

    async def enrolled(self, item: Item) -> set:
        if item.course_id not in self._enrolled:
            self._enrolled[item.course_id] = set((await self.db.execute(
                select(TrailRun.user_id).where(TrailRun.course_id == item.course_id, TrailRun.org_id == item.org_id)
            )).scalars().all())
        return self._enrolled[item.course_id]

    async def can_read(self, item: Item, user_id: int) -> bool:
        key = (item.course_id, user_id)
        if key not in self._allowed:
            self._allowed[key] = await self._check(item, user_id)
        return self._allowed[key]

    async def _check(self, item: Item, user_id: int) -> bool:
        try:
            if user_id not in self._users:
                user = await self.db.get(User, user_id)
                self._users[user_id] = PublicUser.model_validate(user.model_dump()) if user is not None else None
            reader = self._users[user_id]
            if reader is None:
                return False
            decision = await check_resource_access(
                None,  # type: ignore[arg-type]
                self.db,
                reader,
                item.course_uuid,
                AccessAction.READ,
                raise_on_deny=False,
            )
            return bool(decision.allowed)
        except Exception:
            # Unsure means no: a missed email is better than one to someone
            # who lost access.
            logger.warning("Course access check failed for user %s", user_id, exc_info=True)
            return False


async def run_assignments(db: AsyncSession, now: Optional[datetime] = None) -> Stats:
    """``now`` is timezone-aware."""
    stats = Stats()
    now = now or datetime.now(timezone.utc)

    items = await _load_items(db)
    cursor = await db.get(EmailNotificationCursor, CURSOR_ID)
    if cursor is None or not cursor.assignments_seeded:
        await _seed(db, cursor, items)
        return stats

    states = {
        s.assignment_id: s for s in (await db.execute(select(EmailNotificationAssignmentState))).scalars().all()
    }

    # (item, kind) for every change, recorded before anything is sent.
    events: list = []
    for item in items:
        previous = states.get(item.assignment_id)
        if previous is not None and previous.visible == item.visible and previous.due_date == item.due_raw:
            continue
        kind = _change(item, previous, now)
        if await _record(db, item, previous) and kind is not None:
            events.append((item, kind))

    due_soon = [
        item for item in items
        if item.visible
        and (due := due_moment(item.due_raw)) is not None
        and (anchor := reminder_anchor(item.due_raw)) is not None
        and anchor - MAX_LEAD <= now < due
    ]
    stats.considered = len(events) + len(due_soon)
    if not events and not due_soon:
        return stats

    audiences: dict = {}

    async def audience_for(org_id: int) -> OrgAudience:
        if org_id not in audiences:
            audiences[org_id] = await load_org_audience(db, org_id)
        return audiences[org_id]

    readers = Readers(db)
    looks: dict = {}
    # (user id, org id) -> entries
    found: dict = {}
    announced: set = set()

    async def look_for(org_id: int):
        if org_id not in looks:
            looks[org_id] = await load_org_look(db, org_id)
        return looks[org_id]

    async def add(item: Item, user_id: int, kind: str, audience: OrgAudience) -> None:
        look = await look_for(item.org_id)
        if look is None:
            return
        found.setdefault((user_id, item.org_id), []).append(emails.AssignmentEntry(
            kind=kind,
            title=item.title,
            course_name=item.course_name,
            due_text=due_text(item.due_raw, audience.settings.timezone),
            url=activity_url(look.base_url, item),
        ))

    for item, kind in events:
        audience = await audience_for(item.org_id)
        if not audience.settings.enabled or not audience.settings.assignments_enabled:
            continue
        for user_id in await readers.enrolled(item):
            if user_id not in audience.members or not audience.preference(user_id).assignment_updates:
                continue
            if not await readers.can_read(item, user_id):
                continue
            announced.add((item.assignment_id, user_id))
            await add(item, user_id, kind, audience)

    for item in due_soon:
        audience = await audience_for(item.org_id)
        if not audience.settings.enabled or not audience.settings.assignments_enabled:
            continue
        anchor = reminder_anchor(item.due_raw)
        assert anchor is not None
        enrolled = await readers.enrolled(item)
        if not enrolled:
            continue
        done = set((await db.execute(
            select(AssignmentUserSubmission.user_id).where(
                AssignmentUserSubmission.assignment_id == item.assignment_id,
                AssignmentUserSubmission.submission_status.in_(_DONE),  # type: ignore[attr-defined]
            )
        )).scalars().all())
        reminded = set((await db.execute(
            select(EmailNotificationReminder.user_id).where(
                EmailNotificationReminder.assignment_id == item.assignment_id,
                EmailNotificationReminder.due_date == item.due_raw,
            )
        )).scalars().all())
        for user_id in enrolled - done - reminded:
            if user_id not in audience.members:
                continue
            pref = audience.preference(user_id)
            if not pref.assignment_reminders or now < anchor - timedelta(days=pref.reminder_days):
                continue
            if not await readers.can_read(item, user_id):
                continue
            if not await _claim_reminder(db, item.assignment_id, user_id, item.due_raw):
                continue
            # Just told about this assignment and its due date in the same
            # email: the reminder would say it twice.
            if (item.assignment_id, user_id) not in announced:
                await add(item, user_id, emails.KIND_REMINDER, audience)

    if not found:
        return stats

    recipients = await mailable_users(db, {user_id for user_id, _ in found})
    for (user_id, org_id), entries in found.items():
        user = recipients.get(user_id)
        if user is None:
            continue
        look = looks[org_id]
        try:
            sent = await asyncio.to_thread(
                emails.send_assignment_email,
                email=user.email,
                username=user.username,
                org_name=look.branding.org_name,
                entries=entries,
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
            logger.warning("Assignment email failed for user %s", user_id, exc_info=True)
    return stats
