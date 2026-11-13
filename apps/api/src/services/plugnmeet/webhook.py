"""Verified plugNmeet webhooks -> attendance.

plugNmeet signs every webhook with an HS256 JWT (the API secret as key,
``iss`` = API key, valid five minutes) whose ``sha256`` claim is the base64
SHA-256 of the exact body, sent in ``Authorization`` and ``Hash-Token``.
Anything that does not verify is rejected before the body is parsed. A
verified token is accepted once (replay cache), so a captured request cannot
be resent to inflate attendance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import jwt
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.live_classes import LiveClass, LiveClassAttendance
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.services.plugnmeet.classes import user_uuid_from_plugnmeet_id
from src.services.plugnmeet.config import PlugNmeetSettings

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 64 * 1024
LEEWAY_SECONDS = 30
REPLAY_TTL_SECONDS = 10 * 60


class WebhookRejected(Exception):
    pass


def _redis():
    from src.core.redis import get_redis_client

    return get_redis_client()


def verify(settings: PlugNmeetSettings, token: Optional[str], body: bytes) -> dict[str, Any]:
    """The decoded event, or WebhookRejected."""
    if not token:
        raise WebhookRejected("missing signature")
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        claims = jwt.decode(
            token,
            settings.api_secret,
            algorithms=["HS256"],
            issuer=settings.api_key,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "sha256"], "verify_aud": False},
        )
    except jwt.PyJWTError as exc:
        raise WebhookRejected(f"bad token ({type(exc).__name__})") from exc
    expected = base64.b64encode(hashlib.sha256(body).digest()).decode()
    sent = claims.get("sha256")
    if not isinstance(sent, str) or not hmac.compare_digest(sent, expected):
        raise WebhookRejected("body hash mismatch")

    cache = _redis()
    if cache is None:
        # Without a replay cache a captured request could be replayed for
        # five minutes. Refuse instead; plugNmeet retries.
        raise WebhookRejected("replay cache unavailable")
    key = "plugnmeet:webhook:" + hashlib.sha256(token.encode()).hexdigest()
    if not cache.set(key, "1", ex=REPLAY_TTL_SECONDS, nx=True):
        raise WebhookRejected("replayed")

    try:
        event = json.loads(body)
    except ValueError as exc:
        raise WebhookRejected("not JSON") from exc
    if not isinstance(event, dict):
        raise WebhookRejected("not an object")
    return event


async def _class_for_room(db: AsyncSession, room_id: Any) -> Optional[LiveClass]:
    if not isinstance(room_id, str) or not room_id.startswith("lh-") or room_id.startswith("lh-quick-"):
        return None
    return (await db.execute(
        select(LiveClass).where(LiveClass.class_uuid == "liveclass_" + room_id[3:])
    )).scalars().first()


async def _member(db: AsyncSession, identity: Any, org_id: int) -> Optional[User]:
    if not isinstance(identity, str):
        return None
    user_uuid = user_uuid_from_plugnmeet_id(identity)
    if user_uuid is None:
        return None
    user = (await db.execute(select(User).where(User.user_uuid == user_uuid))).scalars().first()
    if user is None:
        return None
    membership = (await db.execute(
        select(UserOrganization.id).where(UserOrganization.user_id == user.id, UserOrganization.org_id == org_id)
    )).first()
    return user if membership else None


async def _close_open(db: AsyncSession, class_id: int, now: datetime, user_id: Optional[int] = None) -> None:
    query = select(LiveClassAttendance).where(
        LiveClassAttendance.class_id == class_id,
        LiveClassAttendance.left_at == None,  # noqa: E711
    )
    if user_id is not None:
        query = query.where(LiveClassAttendance.user_id == user_id)
    for row in (await db.execute(query)).scalars().all():
        row.left_at = now
        db.add(row)


async def record(db: AsyncSession, event: dict[str, Any], now: Optional[datetime] = None) -> str:
    """Apply one verified event. Returns what was done (for logs and tests).
    Times are the server's receive time, not values from the payload."""
    now = now or datetime.now(timezone.utc)
    name = str(event.get("event") or "")
    room = event.get("room") if isinstance(event.get("room"), dict) else {}
    live_class = await _class_for_room(db, room.get("room_id"))
    if live_class is None or live_class.id is None:
        return "ignored"

    if name in ("room_finished", "session_ended"):
        await _close_open(db, live_class.id, now)
        await db.commit()
        _forget_active(room.get("room_id"))
        return "closed"
    if name not in ("participant_joined", "participant_left"):
        return "ignored"

    participant = event.get("participant") if isinstance(event.get("participant"), dict) else {}
    user = await _member(db, participant.get("identity"), live_class.org_id)
    if user is None or user.id is None:
        return "ignored"
    # Close a stay that never got its "left" (dropped connection) first, so
    # a reconnect does not count the overlap twice.
    await _close_open(db, live_class.id, now, user.id)
    if name == "participant_joined":
        db.add(LiveClassAttendance(class_id=live_class.id, user_id=user.id, joined_at=now))
    await db.commit()
    return "joined" if name == "participant_joined" else "left"


def _forget_active(room_id: Any) -> None:
    cache = _redis()
    if cache is None or not isinstance(room_id, str):
        return
    try:
        cache.delete(f"plugnmeet:active:{room_id}")
    except Exception:  # pragma: no cover - cache only
        logger.debug("plugNmeet active cache delete failed", exc_info=True)
