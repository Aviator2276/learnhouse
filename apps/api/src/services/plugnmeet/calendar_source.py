"""Live classes on the LearnHouse calendar (``/calendar``).

Registered through the calendar's own source registry, so no upstream
calendar code is modified. Uses the calendar's ``live_session`` event type,
which the calendar UI already styles. Status is derived from the schedule
(no plugNmeet call per event): the class page shows the real room state.
"""

from __future__ import annotations

from sqlmodel import select

from src.db.courses.courses import Course
from src.db.live_classes import LiveClass
from src.services.calendar.sources import register_source
from src.services.calendar.types import CalendarContext, CalendarEvent, course_ref, iso_utc
from src.services.plugnmeet.classes import _utc, class_path
from src.services.plugnmeet.config import get_plugnmeet_settings
from src.services.plugnmeet.permissions import org_access


async def live_classes(ctx: CalendarContext) -> list[CalendarEvent]:
    if not get_plugnmeet_settings().enabled:
        return []
    access = await org_access(ctx.request, ctx.db_session, ctx.current_user, ctx.org.id)
    if not access.is_member:
        return []
    rows = (await ctx.db_session.execute(
        select(LiveClass, Course)
        .outerjoin(Course, Course.id == LiveClass.course_id)  # type: ignore[arg-type]
        .where(
            LiveClass.org_id == ctx.org.id,
            LiveClass.cancelled == False,  # noqa: E712
            LiveClass.starts_at < ctx.end,
            LiveClass.ends_at > ctx.start,
        )
        .order_by(LiveClass.starts_at)
        .limit(1000)
    )).all()
    events = []
    for live_class, course in rows:
        if not await access.can_view(course):
            continue
        starts, ends = _utc(live_class.starts_at), _utc(live_class.ends_at)
        if ends < ctx.now:
            state = "ended"
        elif starts <= ctx.now:
            state = "live"
        else:
            state = "scheduled"
        events.append(CalendarEvent(
            id=f"live_class:{live_class.class_uuid}",
            type="live_session",
            title=live_class.title,
            starts_at=iso_utc(starts),
            ends_at=iso_utc(ends),
            url=class_path(live_class),
            course=course_ref(course),
            status=state,
            meta={"provider": "plugnmeet"},
        ))
    return events


register_source(live_classes, key="fork_plugnmeet_live_classes")
