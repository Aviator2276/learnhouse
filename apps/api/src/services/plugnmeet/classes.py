"""Scheduling live classes, their status, and joining the room."""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.live_classes import LiveClass
from src.db.users import User
from src.services.plugnmeet.client import PlugNmeetClient, PlugNmeetError, public_image_url, safe_id
from src.services.plugnmeet.config import PlugNmeetSettings
from src.services.plugnmeet.permissions import OrgAccess

logger = logging.getLogger(__name__)

MIN_DURATION, MAX_DURATION = 15, 8 * 60
MAX_OCCURRENCES = 52
# How long a plugNmeet "is this room live" answer is reused.
ACTIVE_CACHE_SECONDS = 15
# The live-now banner also shows classes starting this soon.
SOON_MINUTES = 10


class Frequency(str, Enum):
    none = "none"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class Scope(str, Enum):
    this = "this"
    following = "following"


def _utc(value: datetime) -> datetime:
    """Aware UTC. SQLite (tests) hands back naive datetimes; they are UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown time zone") from exc


class ClassCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    starts_at: datetime
    duration_minutes: int = Field(ge=MIN_DURATION, le=MAX_DURATION)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    course_uuid: Optional[str] = Field(default=None, max_length=100)
    frequency: Frequency = Frequency.none
    occurrences: int = Field(default=1, ge=1, le=MAX_OCCURRENCES)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("starts_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("starts_at must include a UTC offset")
        return value


class ClassUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    starts_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, ge=MIN_DURATION, le=MAX_DURATION)
    timezone: Optional[str] = Field(default=None, min_length=1, max_length=64)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value

    @field_validator("starts_at")
    @classmethod
    def _aware(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("starts_at must include a UTC offset")
        return value


class CourseRef(BaseModel):
    course_uuid: str
    name: str


class ClassOut(BaseModel):
    class_uuid: str
    org_id: int
    title: str
    description: str
    starts_at: str
    ends_at: str
    duration_minutes: int
    timezone: str
    series_uuid: Optional[str]
    course: Optional[CourseRef]
    # scheduled | live | ended | cancelled
    status: str
    can_moderate: bool
    # A participant can enter right now (the room is live).
    can_join: bool
    # A moderator can open the room right now.
    can_start: bool


def iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def room_id_for(live_class: LiveClass) -> str:
    return "lh-" + safe_id(live_class.class_uuid.removeprefix("liveclass_"))


def class_path(live_class: LiveClass) -> str:
    """Org-relative web path of the class page."""
    return "/live-classes/" + live_class.class_uuid.removeprefix("liveclass_")


def normalize_class_uuid(value: str) -> str:
    """Accept the URL form (bare UUID) or the stored ``liveclass_`` form."""
    value = value.strip()
    return value if value.startswith("liveclass_") else "liveclass_" + value


def quick_room_id(org_id: int) -> str:
    return f"lh-quick-{int(org_id)}"


def occurrence_starts(first: datetime, zone_name: str, frequency: Frequency, count: int) -> list[datetime]:
    """UTC starts of a series, repeating at the same local wall-clock time in
    ``zone_name`` (so 10:00 stays 10:00 across daylight-saving changes).
    Monthly repeats on the same day of the month, or the month's last day."""
    zone = _zone(zone_name)
    local = _utc(first).astimezone(zone).replace(tzinfo=None)
    if frequency == Frequency.none:
        count = 1
    starts: list[datetime] = []
    for i in range(count):
        if frequency == Frequency.daily:
            wall = local + timedelta(days=i)
        elif frequency == Frequency.weekly:
            wall = local + timedelta(weeks=i)
        elif frequency == Frequency.monthly:
            month_index = local.month - 1 + i
            year, month = local.year + month_index // 12, month_index % 12 + 1
            day = min(local.day, calendar.monthrange(year, month)[1])
            wall = local.replace(year=year, month=month, day=day)
        else:
            wall = local
        starts.append(wall.replace(tzinfo=zone).astimezone(timezone.utc))
    return starts


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return str(datetime.now())


# ---------------------------------------------------------------------------
# Room liveness, cached briefly in Redis
# ---------------------------------------------------------------------------


def _redis():
    from src.core.redis import get_redis_client

    return get_redis_client()


def _cache_active(room_id: str, active: bool) -> None:
    client = _redis()
    if client is None:
        return
    try:
        client.set(f"plugnmeet:active:{room_id}", "1" if active else "0", ex=ACTIVE_CACHE_SECONDS)
    except Exception:  # pragma: no cover - cache only
        logger.debug("plugNmeet active cache write failed", exc_info=True)


async def room_is_active(client: PlugNmeetClient, room_id: str) -> bool:
    """Whether the room is live. Raises PlugNmeetError when plugNmeet can't
    say, so callers never mistake an outage for "not started"."""
    cache = _redis()
    if cache is not None:
        try:
            cached = cache.get(f"plugnmeet:active:{room_id}")
        except Exception:  # pragma: no cover - cache only
            cached = None
        if cached is not None:
            return cached in (b"1", "1")
    active = await client.is_room_active(room_id)
    _cache_active(room_id, active)
    return active


def in_start_window(settings: PlugNmeetSettings, live_class: LiveClass, now: datetime) -> bool:
    opens = _utc(live_class.starts_at) - timedelta(minutes=settings.start_early_minutes)
    closes = _utc(live_class.ends_at) + timedelta(minutes=settings.end_grace_minutes)
    return not live_class.cancelled and opens <= now <= closes


def might_be_live(settings: PlugNmeetSettings, live_class: LiveClass, now: datetime) -> bool:
    """Only classes inside their start window can have a room; asking
    plugNmeet about the others would be wasted calls."""
    return in_start_window(settings, live_class, now)


async def serialize(
    access: OrgAccess,
    settings: PlugNmeetSettings,
    client: PlugNmeetClient,
    live_class: LiveClass,
    course: Optional[Course],
    *,
    check_live: bool = True,
    now: Optional[datetime] = None,
) -> ClassOut:
    now = now or _now()
    can_moderate = await access.can_moderate(course)
    active = False
    if check_live and might_be_live(settings, live_class, now):
        try:
            active = await room_is_active(client, room_id_for(live_class))
        except PlugNmeetError as exc:
            logger.warning("plugNmeet status check failed: %s", exc)
    if live_class.cancelled:
        state = "cancelled"
    elif active:
        state = "live"
    elif _utc(live_class.ends_at) < now:
        state = "ended"
    else:
        state = "scheduled"
    starts, ends = _utc(live_class.starts_at), _utc(live_class.ends_at)
    return ClassOut(
        class_uuid=live_class.class_uuid,
        org_id=live_class.org_id,
        title=live_class.title,
        description=live_class.description,
        starts_at=iso(starts),
        ends_at=iso(ends),
        duration_minutes=int((ends - starts).total_seconds() // 60),
        timezone=live_class.timezone,
        series_uuid=live_class.series_uuid,
        course=CourseRef(course_uuid=course.course_uuid, name=course.name) if course else None,
        status=state,
        can_moderate=can_moderate,
        can_join=active and not live_class.cancelled,
        can_start=can_moderate and in_start_window(settings, live_class, now),
    )


# ---------------------------------------------------------------------------
# Loading and listing
# ---------------------------------------------------------------------------


async def course_by_uuid(db: AsyncSession, org_id: int, course_uuid: str) -> Course:
    course = (await db.execute(
        select(Course).where(Course.course_uuid == course_uuid, Course.org_id == org_id)
    )).scalars().first()
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return course


async def load_class(db: AsyncSession, class_uuid: str) -> tuple[LiveClass, Optional[Course]]:
    live_class = (await db.execute(
        select(LiveClass).where(LiveClass.class_uuid == class_uuid)
    )).scalars().first()
    if live_class is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live class not found")
    course = None
    if live_class.course_id is not None:
        course = await db.get(Course, live_class.course_id)
    return live_class, course


async def visible_classes(
    access: OrgAccess,
    *,
    when: str,
    settings: PlugNmeetSettings,
    course: Optional[Course] = None,
    limit: int = 50,
    now: Optional[datetime] = None,
) -> list[tuple[LiveClass, Optional[Course]]]:
    """Classes the caller may see. ``when``: ``upcoming`` (not yet ended,
    soonest first), ``past`` (ended, latest first) or ``now`` (inside the
    window in which a room can be open, not cancelled)."""
    now = now or _now()
    db = access.db
    query = select(LiveClass, Course).outerjoin(Course, Course.id == LiveClass.course_id).where(  # type: ignore[arg-type]
        LiveClass.org_id == access.org_id
    )
    if course is not None:
        query = query.where(LiveClass.course_id == course.id)
    if when == "past":
        query = query.where(LiveClass.ends_at < now).order_by(LiveClass.starts_at.desc())  # type: ignore[attr-defined]
    elif when == "now":
        query = query.where(
            LiveClass.cancelled == False,  # noqa: E712
            LiveClass.starts_at <= now + timedelta(minutes=max(SOON_MINUTES, settings.start_early_minutes)),
            LiveClass.ends_at >= now - timedelta(minutes=settings.end_grace_minutes),
        ).order_by(LiveClass.starts_at)
    else:
        query = query.where(LiveClass.ends_at >= now).order_by(LiveClass.starts_at)
    rows = (await db.execute(query.limit(500))).all()
    out: list[tuple[LiveClass, Optional[Course]]] = []
    for live_class, row_course in rows:
        if not await access.can_view(row_course):
            continue
        out.append((live_class, row_course))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------


async def create_classes(access: OrgAccess, body: ClassCreate) -> list[LiveClass]:
    db = access.db
    course = None
    if body.course_uuid:
        course = await course_by_uuid(db, access.org_id, body.course_uuid)
        if not await access.can_view(course):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    if not await access.can_moderate(course):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can't schedule live classes here")

    _zone(body.timezone)
    starts = occurrence_starts(body.starts_at, body.timezone, body.frequency, body.occurrences)
    series_uuid = f"liveseries_{uuid4()}" if len(starts) > 1 else None
    stamp = _stamp()
    rows = []
    for start in starts:
        row = LiveClass(
            class_uuid=f"liveclass_{uuid4()}",
            org_id=access.org_id,
            course_id=course.id if course else None,
            series_uuid=series_uuid,
            title=body.title,
            description=body.description,
            starts_at=start,
            ends_at=start + timedelta(minutes=body.duration_minutes),
            timezone=body.timezone,
            created_by_id=access.user_id,
            creation_date=stamp,
            update_date=stamp,
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


async def scoped_rows(db: AsyncSession, live_class: LiveClass, scope: Scope) -> list[LiveClass]:
    """This occurrence, or it and every later one of the same series."""
    if scope == Scope.this or not live_class.series_uuid:
        return [live_class]
    return list((await db.execute(
        select(LiveClass)
        .where(LiveClass.series_uuid == live_class.series_uuid, LiveClass.starts_at >= live_class.starts_at)
        .order_by(LiveClass.starts_at)
    )).scalars().all())


async def update_classes(db: AsyncSession, live_class: LiveClass, body: ClassUpdate, scope: Scope) -> list[LiveClass]:
    rows = await scoped_rows(db, live_class, scope)
    zone_name = body.timezone or live_class.timezone
    zone = _zone(zone_name)
    # A new start moves every selected occurrence by the same wall-clock shift.
    shift: Optional[timedelta] = None
    if body.starts_at is not None:
        old_local = _utc(live_class.starts_at).astimezone(_zone(live_class.timezone)).replace(tzinfo=None)
        new_local = _utc(body.starts_at).astimezone(zone).replace(tzinfo=None)
        shift = new_local - old_local
    stamp = _stamp()
    for row in rows:
        start = _utc(row.starts_at)
        duration = _utc(row.ends_at) - start
        if body.duration_minutes is not None:
            duration = timedelta(minutes=body.duration_minutes)
        if shift is not None or body.timezone is not None:
            wall = start.astimezone(_zone(row.timezone)).replace(tzinfo=None) + (shift or timedelta())
            start = wall.replace(tzinfo=zone).astimezone(timezone.utc)
            row.timezone = zone_name
        row.starts_at = start
        row.ends_at = start + duration
        if body.title is not None:
            row.title = body.title
        if body.description is not None:
            row.description = body.description
        row.update_date = stamp
        db.add(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return rows


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


def display_name(user: User) -> str:
    name = " ".join(part for part in ((user.first_name or "").strip(), (user.last_name or "").strip()) if part)
    return (name or user.username or "Member")[:64]


def plugnmeet_user_id(user: User) -> str:
    """Deterministic per user and reversible (it is the user's UUID), so a
    reconnect replaces the stale session and attendance maps straight back."""
    return safe_id(user.user_uuid)


def _lock(room_id: str) -> bool:
    """Best-effort mutex so two moderators clicking Start at once create the
    room once. Without Redis the create is attempted anyway and a duplicate
    is tolerated below."""
    client = _redis()
    if client is None:
        return True
    try:
        return bool(client.set(f"plugnmeet:create:{room_id}", "1", ex=20, nx=True))
    except Exception:  # pragma: no cover - lock only
        return True


async def ensure_room(
    client: PlugNmeetClient,
    room_id: str,
    *,
    title: str,
    welcome_message: str,
    logout_url: str,
    webhook_url: Optional[str],
) -> None:
    if await room_is_active(client, room_id):
        return
    if _lock(room_id):
        try:
            await client.create_room(
                room_id, title=title, welcome_message=welcome_message, logout_url=logout_url, webhook_url=webhook_url
            )
        except PlugNmeetError:
            # Someone else may have created it in the meantime.
            if not await client.is_room_active(room_id):
                raise
    _cache_active(room_id, True)


async def join_room(
    db: AsyncSession,
    settings: PlugNmeetSettings,
    client: PlugNmeetClient,
    *,
    room_id: str,
    user_id: int,
    is_moderator: bool,
    title: str,
    logout_url: str,
    may_start: bool,
    inactive_code: str = "NOT_STARTED",
) -> str:
    """Start the room if a moderator may, then mint a fresh single-use token
    for exactly this user and role. Returns the plugNmeet join URL."""
    user = await db.get(User, user_id)
    if user is None:  # pragma: no cover - the session's user always exists
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to use live classes")
    try:
        if is_moderator and may_start:
            await ensure_room(
                client,
                room_id,
                title=title,
                welcome_message=f"Welcome to {title}",
                logout_url=logout_url,
                webhook_url=f"{settings.public_url}/api/v1/plugnmeet/webhook" if settings.webhook_enabled else None,
            )
        elif not await room_is_active(client, room_id):
            message = "This class has ended." if inactive_code == "ENDED" else "This class hasn't started yet."
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": inactive_code, "message": message},
            )
        token = await client.get_join_token(
            room_id,
            name=display_name(user),
            user_id=plugnmeet_user_id(user),
            is_admin=is_moderator,
            profile_pic=public_image_url(settings.public_url, user.user_uuid, user.avatar_image),
        )
    except PlugNmeetError as exc:
        logger.error("plugNmeet join failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "UNAVAILABLE", "message": "The meeting server is unreachable right now, please try again."},
        ) from exc
    return client.join_url(token)


async def end_room(client: PlugNmeetClient, room_id: str) -> None:
    try:
        await client.end_room(room_id)
    except PlugNmeetError as exc:
        logger.error("plugNmeet end failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "UNAVAILABLE", "message": "The meeting server is unreachable right now, please try again."},
        ) from exc
    _cache_active(room_id, False)
