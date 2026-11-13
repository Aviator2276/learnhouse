"""Live classes on plugNmeet, mounted at ``/plugnmeet``. Fork-only.

See ``src/services/plugnmeet/__init__.py`` for the auth model. Every endpoint
except ``/config`` and ``/webhook`` needs a signed-in member of the class's
organization; ``/webhook`` needs a valid plugNmeet signature instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.live_classes import LiveClassAttendance
from src.db.organizations import Organization
from src.db.users import User
from src.security.auth import get_current_user
from src.services.plugnmeet import calendar_source  # noqa: F401  (registers the calendar source)
from src.services.plugnmeet import classes as svc
from src.services.plugnmeet import webhook
from src.services.plugnmeet.client import PlugNmeetClient
from src.services.plugnmeet.config import PlugNmeetSettings, get_plugnmeet_settings
from src.services.plugnmeet.permissions import OrgAccess, org_access, require_member
from src.services.security.rate_limiting import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

JOINS_PER_MINUTE = 20


def make_client(settings: PlugNmeetSettings) -> PlugNmeetClient:
    return PlugNmeetClient(settings)


def _settings_or_404() -> PlugNmeetSettings:
    settings = get_plugnmeet_settings()
    if not settings.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live classes are not enabled")
    return settings


async def _org(db: AsyncSession, org_id: int) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


async def _member_access(request: Request, db: AsyncSession, current_user, org_id: int) -> OrgAccess:
    await _org(db, org_id)
    access = await org_access(request, db, current_user, org_id)
    require_member(access)
    return access


async def _class_access(request: Request, db: AsyncSession, current_user, class_uuid: str):
    live_class, course = await svc.load_class(db, svc.normalize_class_uuid(class_uuid))
    access = await org_access(request, db, current_user, live_class.org_id)
    require_member(access)
    if not await access.can_view(course):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live class not found")
    return live_class, course, access


async def _require_moderator(access: OrgAccess, course) -> None:
    if not await access.can_moderate(course):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only the class's hosts can do this")


def _join_rate_limit(user_id: int) -> None:
    allowed, _count, retry_after = check_rate_limit(f"plugnmeet_join:{user_id}", JOINS_PER_MINUTE, 60)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Too many join attempts, please wait a moment.", "retry_after": retry_after},
        )


class ConfigOut(BaseModel):
    enabled: bool
    quick_room: bool = False
    attendance: bool = False


class PermissionsOut(BaseModel):
    can_schedule: bool
    can_host_quick_room: bool


class JoinOut(BaseModel):
    join_url: str


class QuickRoomOut(BaseModel):
    enabled: bool
    active: bool
    can_start: bool


class AttendeeOut(BaseModel):
    user_uuid: str
    username: str
    name: str
    first_joined_at: str
    last_left_at: Optional[str]
    total_minutes: int
    in_room: bool


class AttendanceOut(BaseModel):
    enabled: bool
    attendees: list[AttendeeOut]


ScopeQuery = Query(svc.Scope.this, description="this occurrence, or this and the following ones of its series")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@router.get("/config", response_model=ConfigOut, summary="Whether live classes are available")
async def api_config() -> ConfigOut:
    settings = get_plugnmeet_settings()
    if not settings.enabled:
        return ConfigOut(enabled=False)
    return ConfigOut(enabled=True, quick_room=settings.quick_room, attendance=settings.webhook_enabled)


@router.get("/org/{org_id}/permissions", response_model=PermissionsOut)
async def api_permissions(
    request: Request,
    org_id: int,
    course_uuid: Optional[str] = Query(default=None, max_length=100),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PermissionsOut:
    settings = _settings_or_404()
    access = await _member_access(request, db, current_user, org_id)
    course = await svc.course_by_uuid(db, org_id, course_uuid) if course_uuid else None
    if course is not None and not await access.can_view(course):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    return PermissionsOut(
        can_schedule=await access.can_moderate(course),
        can_host_quick_room=settings.quick_room and access.is_staff,
    )


@router.get("/org/{org_id}/classes", response_model=list[svc.ClassOut])
async def api_list_classes(
    request: Request,
    org_id: int,
    when: Literal["upcoming", "past", "now"] = "upcoming",
    course_uuid: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[svc.ClassOut]:
    settings = _settings_or_404()
    access = await _member_access(request, db, current_user, org_id)
    course = None
    if course_uuid:
        course = await svc.course_by_uuid(db, org_id, course_uuid)
        if not await access.can_view(course):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")
    client = make_client(settings)
    now = datetime.now(timezone.utc)
    rows = await svc.visible_classes(access, when=when, settings=settings, course=course, limit=limit, now=now)
    out = [await svc.serialize(access, settings, client, c, co, now=now) for c, co in rows]
    if when == "now":
        soon = now + timedelta(minutes=svc.SOON_MINUTES)
        out = [
            c for c in out
            if c.status == "live" or (c.status == "scheduled" and datetime.fromisoformat(c.starts_at.replace("Z", "+00:00")) <= soon)
        ]
    return out


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


@router.post("/org/{org_id}/classes", response_model=list[svc.ClassOut], status_code=status.HTTP_201_CREATED)
async def api_create_classes(
    request: Request,
    org_id: int,
    body: svc.ClassCreate,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[svc.ClassOut]:
    settings = _settings_or_404()
    access = await _member_access(request, db, current_user, org_id)
    rows = await svc.create_classes(access, body)
    course = None
    if rows and rows[0].course_id is not None:
        course = await svc.course_by_uuid(db, org_id, body.course_uuid or "")
    client = make_client(settings)
    return [await svc.serialize(access, settings, client, row, course, check_live=False) for row in rows]


@router.get("/classes/{class_uuid}", response_model=svc.ClassOut)
async def api_get_class(
    request: Request,
    class_uuid: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> svc.ClassOut:
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    return await svc.serialize(access, settings, make_client(settings), live_class, course)


@router.patch("/classes/{class_uuid}", response_model=list[svc.ClassOut])
async def api_update_class(
    request: Request,
    class_uuid: str,
    body: svc.ClassUpdate,
    scope: svc.Scope = ScopeQuery,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[svc.ClassOut]:
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    await _require_moderator(access, course)
    rows = await svc.update_classes(db, live_class, body, scope)
    client = make_client(settings)
    return [await svc.serialize(access, settings, client, row, course, check_live=False) for row in rows]


async def _end_quietly(client: PlugNmeetClient, room_id: str) -> None:
    try:
        await svc.end_room(client, room_id)
    except HTTPException:
        logger.warning("Could not end plugNmeet room %s", room_id)


@router.post("/classes/{class_uuid}/cancel", response_model=list[svc.ClassOut])
async def api_cancel_class(
    request: Request,
    class_uuid: str,
    scope: svc.Scope = ScopeQuery,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> list[svc.ClassOut]:
    """Cancel, keeping the class and its attendance on record."""
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    await _require_moderator(access, course)
    rows = await svc.scoped_rows(db, live_class, scope)
    client = make_client(settings)
    now = datetime.now(timezone.utc)
    for row in rows:
        if not row.cancelled and svc.might_be_live(settings, row, now):
            await _end_quietly(client, svc.room_id_for(row))
        row.cancelled = True
        db.add(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [await svc.serialize(access, settings, client, row, course, check_live=False) for row in rows]


@router.delete("/classes/{class_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def api_delete_class(
    request: Request,
    class_uuid: str,
    scope: svc.Scope = ScopeQuery,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete, with its attendance."""
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    await _require_moderator(access, course)
    rows = await svc.scoped_rows(db, live_class, scope)
    client = make_client(settings)
    now = datetime.now(timezone.utc)
    for row in rows:
        if svc.might_be_live(settings, row, now):
            await _end_quietly(client, svc.room_id_for(row))
        for attendance in (await db.execute(
            select(LiveClassAttendance).where(LiveClassAttendance.class_id == row.id)
        )).scalars().all():
            await db.delete(attendance)
        await db.delete(row)
    await db.commit()


# ---------------------------------------------------------------------------
# The room
# ---------------------------------------------------------------------------


@router.post("/classes/{class_uuid}/join", response_model=JoinOut)
async def api_join_class(
    request: Request,
    class_uuid: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> JoinOut:
    """Start (moderators) or enter the room. Returns a single-use plugNmeet
    URL to open straight away; never cache or share it."""
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    if live_class.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "CANCELLED", "message": "This class was cancelled."},
        )
    _join_rate_limit(access.user_id)
    is_moderator = await access.can_moderate(course)
    now = datetime.now(timezone.utc)
    may_start = svc.in_start_window(settings, live_class, now)
    if is_moderator and not may_start and svc._utc(live_class.starts_at) > now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "TOO_EARLY", "message": f"You can open the room up to {settings.start_early_minutes} minutes before the start."},
        )
    ended = svc._utc(live_class.ends_at) < now
    url = await svc.join_room(
        db,
        settings,
        make_client(settings),
        room_id=svc.room_id_for(live_class),
        user_id=access.user_id,
        is_moderator=is_moderator,
        title=live_class.title,
        logout_url=settings.public_url + svc.class_path(live_class),
        may_start=may_start,
        inactive_code="ENDED" if ended else "NOT_STARTED",
    )
    return JoinOut(join_url=url)


@router.post("/classes/{class_uuid}/end", status_code=status.HTTP_204_NO_CONTENT)
async def api_end_class(
    request: Request,
    class_uuid: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    await _require_moderator(access, course)
    await svc.end_room(make_client(settings), svc.room_id_for(live_class))


@router.get("/classes/{class_uuid}/attendance", response_model=AttendanceOut)
async def api_attendance(
    request: Request,
    class_uuid: str,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AttendanceOut:
    settings = _settings_or_404()
    live_class, course, access = await _class_access(request, db, current_user, class_uuid)
    await _require_moderator(access, course)
    rows = (await db.execute(
        select(LiveClassAttendance, User)
        .join(User, User.id == LiveClassAttendance.user_id)  # type: ignore[arg-type]
        .where(LiveClassAttendance.class_id == live_class.id)
        .order_by(LiveClassAttendance.joined_at)
    )).all()
    now = datetime.now(timezone.utc)
    by_user: dict[int, AttendeeOut] = {}
    seconds: dict[int, float] = {}
    for stay, user in rows:
        joined = svc._utc(stay.joined_at)
        left = svc._utc(stay.left_at) if stay.left_at else None
        seconds[user.id] = seconds.get(user.id, 0.0) + max(0.0, ((left or now) - joined).total_seconds())
        entry = by_user.get(user.id)
        if entry is None:
            entry = AttendeeOut(
                user_uuid=user.user_uuid,
                username=user.username,
                name=svc.display_name(user),
                first_joined_at=svc.iso(joined),
                last_left_at=None,
                total_minutes=0,
                in_room=False,
            )
            by_user[user.id] = entry
        entry.in_room = left is None
        entry.last_left_at = svc.iso(left) if left else entry.last_left_at
        entry.total_minutes = int(seconds[user.id] // 60)
    return AttendanceOut(enabled=settings.webhook_enabled, attendees=list(by_user.values()))


# ---------------------------------------------------------------------------
# Quick meeting room (one per organization)
# ---------------------------------------------------------------------------


def _quick_or_404(settings: PlugNmeetSettings) -> None:
    if not settings.quick_room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The quick meeting room is not enabled")


@router.get("/org/{org_id}/quick", response_model=QuickRoomOut)
async def api_quick_status(
    request: Request,
    org_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> QuickRoomOut:
    settings = _settings_or_404()
    access = await _member_access(request, db, current_user, org_id)
    if not settings.quick_room:
        return QuickRoomOut(enabled=False, active=False, can_start=False)
    try:
        active = await svc.room_is_active(make_client(settings), svc.quick_room_id(org_id))
    except svc.PlugNmeetError as exc:
        logger.warning("plugNmeet quick room status failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "UNAVAILABLE", "message": "The meeting server is unreachable right now, please try again."},
        ) from exc
    return QuickRoomOut(enabled=True, active=active, can_start=access.is_staff)


@router.post("/org/{org_id}/quick/join", response_model=JoinOut)
async def api_quick_join(
    request: Request,
    org_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> JoinOut:
    """Staff (admins, maintainers) open the org's meeting room; any member
    may join while it is open."""
    settings = _settings_or_404()
    _quick_or_404(settings)
    access = await _member_access(request, db, current_user, org_id)
    _join_rate_limit(access.user_id)
    org = await _org(db, org_id)
    url = await svc.join_room(
        db,
        settings,
        make_client(settings),
        room_id=svc.quick_room_id(org_id),
        user_id=access.user_id,
        is_moderator=access.is_staff,
        title=f"{org.name} meeting room"[:200],
        logout_url=settings.public_url + "/live-classes",
        may_start=access.is_staff,
    )
    return JoinOut(join_url=url)


@router.post("/org/{org_id}/quick/end", status_code=status.HTTP_204_NO_CONTENT)
async def api_quick_end(
    request: Request,
    org_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    settings = _settings_or_404()
    _quick_or_404(settings)
    access = await _member_access(request, db, current_user, org_id)
    if not access.is_staff:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admins and maintainers can end the meeting room")
    await svc.end_room(make_client(settings), svc.quick_room_id(org_id))


# ---------------------------------------------------------------------------
# Webhook (attendance)
# ---------------------------------------------------------------------------


@router.post("/webhook", status_code=status.HTTP_200_OK, include_in_schema=False)
async def api_webhook(request: Request, db: AsyncSession = Depends(get_db_session)) -> dict:
    settings = _settings_or_404()
    if not settings.webhook_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    declared = request.headers.get("content-length")
    if declared and (not declared.isdigit() or int(declared) > webhook.MAX_BODY_BYTES):
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Too large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > webhook.MAX_BODY_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Too large")
    token = request.headers.get("hash-token") or request.headers.get("authorization")
    try:
        event = webhook.verify(settings, token, body)
    except webhook.WebhookRejected as exc:
        logger.warning("Rejected plugNmeet webhook: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature") from exc
    result = await webhook.record(db, event)
    return {"ok": True, "result": result}
