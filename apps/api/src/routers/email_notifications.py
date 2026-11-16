"""Email notifications for communities and assignments, mounted at ``/email-notifications``.
Fork-only.

A member reads and changes their own choices; organization admins and
maintainers change the organization's settings and can send themselves a
test email. ``/unsubscribe`` needs no session: the signed token in the email
link is the authorization, and it can only turn email off.
"""

from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime
from typing import Any, List, Literal, Optional
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.communities.discussions import DISCUSSION_LABELS
from src.db.organizations import Organization
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, APITokenUser, User
from src.routers.nudges import _BUTTON_STYLE, _expired_page, _page
from src.security.auth import get_current_user
from src.security.rbac.constants import is_admin_or_maintainer
from src.services.email_notifications import emails
from src.services.email_notifications import preferences as store
from src.services.email_notifications.audience import effective_preference, get_settings, load_org_audience
from src.services.email_notifications.config import get_config
from src.services.email_notifications.delivery import load_org_look
from src.services.email_notifications.digest import build_for_member, load_authors, week_threads
from src.services.email_notifications.rules import LABEL_IDS, REASON_STAFF, REMINDER_DAY_CHOICES
from src.services.nudges.preferences import get_user_by_uuid
from src.services.security.rate_limiting import check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

Level = Literal["off", "mentions", "replies", "highlights", "all"]
ReminderDays = Literal[1, 2, 3, 7]
assert ReminderDays.__args__ == REMINDER_DAY_CHOICES  # type: ignore[attr-defined]
TESTS_PER_WINDOW = 5
TEST_WINDOW_SECONDS = 10 * 60
MAX_MUTED = 500


def _user_id(current_user: Any) -> int:
    """The id of a real signed-in user, or 401. API tokens have no inbox."""
    if (
        current_user is None
        or isinstance(current_user, (AnonymousUser, APITokenUser))
        or not getattr(current_user, "id", None)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to manage email notifications")
    return int(current_user.id)


async def _membership(db: AsyncSession, current_user: Any, org_id: int) -> UserOrganization:
    user_id = _user_id(current_user)
    membership = (await db.execute(
        select(UserOrganization).where(UserOrganization.user_id == user_id, UserOrganization.org_id == org_id)
    )).scalars().first()
    if membership is None:
        # 404, not 403: a non-member learns nothing about the organization.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return membership


async def _staff(db: AsyncSession, current_user: Any, org_id: int) -> UserOrganization:
    membership = await _membership(db, current_user, org_id)
    if not is_admin_or_maintainer(membership.role_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only organization admins and maintainers can change email notification settings",
        )
    return membership


class CommunityChoice(BaseModel):
    community_uuid: str
    name: str
    muted: bool


class LabelChoice(BaseModel):
    id: str
    name: str
    muted: bool


class PreferencesOut(BaseModel):
    # False when notification email is switched off for the instance or the organization.
    available: bool
    level: Level
    # True until the member saves a choice of their own.
    uses_default: bool
    default_level: Level
    weekly_digest: bool
    digest_available: bool
    digest_weekday: int
    digest_hour: int
    timezone: str
    communities: List[CommunityChoice]
    labels: List[LabelChoice]
    assignments_available: bool
    assignment_updates: bool
    assignment_reminders: bool
    assignment_reminder_days: ReminderDays
    can_manage: bool


class PreferencesIn(BaseModel):
    level: Level
    weekly_digest: bool = True
    muted_community_uuids: List[str] = Field(default_factory=list, max_length=MAX_MUTED)
    muted_labels: List[str] = Field(default_factory=list, max_length=len(LABEL_IDS))
    assignment_updates: bool = True
    assignment_reminders: bool = True
    # None follows the organization's reminder time.
    assignment_reminder_days: Optional[ReminderDays] = None


class SettingsOut(BaseModel):
    # False when LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED is set on the server.
    instance_enabled: bool
    enabled: bool
    default_level: Level
    digest_enabled: bool
    digest_weekday: int
    digest_hour: int
    timezone: str
    assignments_enabled: bool
    assignment_reminder_days: ReminderDays
    last_digest_at: Optional[datetime] = None
    delay_minutes: int


class SettingsIn(BaseModel):
    enabled: bool
    default_level: Level
    digest_enabled: bool
    digest_weekday: int = Field(ge=0, le=6)
    digest_hour: int = Field(ge=0, le=23)
    timezone: str = Field(min_length=1, max_length=64)
    assignments_enabled: bool = True
    assignment_reminder_days: ReminderDays = 1

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        if value not in available_timezones():
            raise ValueError("Unknown time zone")
        return value


class TestIn(BaseModel):
    kind: Literal["instant", "digest", "assignment"]


class TestOut(BaseModel):
    sent_to: str


async def _preferences_out(db: AsyncSession, user_id: int, org_id: int, role_id: int) -> PreferencesOut:
    audience = await load_org_audience(db, org_id)
    settings = audience.settings
    row = audience.preferences.get(user_id)
    pref = audience.preference(user_id)
    communities = await store.readable_communities(db, audience, user_id)
    return PreferencesOut(
        available=get_config().enabled and settings.enabled,
        level=pref.level,  # type: ignore[arg-type]
        uses_default=row is None or row.level is None,
        default_level=audience.preference(0).level,  # type: ignore[arg-type]
        weekly_digest=pref.weekly_digest,
        digest_available=settings.digest_enabled,
        digest_weekday=settings.digest_weekday,
        digest_hour=settings.digest_hour,
        timezone=settings.timezone,
        communities=[
            CommunityChoice(community_uuid=c.community_uuid, name=c.name, muted=c.id in pref.muted_communities)
            for c in communities
        ],
        labels=[
            LabelChoice(id=label["id"], name=label["name"], muted=label["id"] in pref.muted_labels)
            for label in DISCUSSION_LABELS
        ],
        assignments_available=get_config().enabled and settings.enabled and settings.assignments_enabled,
        assignment_updates=pref.assignment_updates,
        assignment_reminders=pref.assignment_reminders,
        assignment_reminder_days=pref.reminder_days,  # type: ignore[arg-type]
        can_manage=is_admin_or_maintainer(role_id),
    )


@router.get("/org/{org_id}/preferences", response_model=PreferencesOut)
async def api_get_preferences(
    org_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PreferencesOut:
    """The caller's community email choices in this organization."""
    membership = await _membership(db, current_user, org_id)
    return await _preferences_out(db, membership.user_id, org_id, membership.role_id)


@router.put("/org/{org_id}/preferences", response_model=PreferencesOut)
async def api_put_preferences(
    org_id: int,
    body: PreferencesIn,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> PreferencesOut:
    """Save the caller's choices. Communities the caller cannot read are
    ignored, so the answer never confirms that one exists."""
    membership = await _membership(db, current_user, org_id)
    audience = await load_org_audience(db, org_id)
    readable = {c.community_uuid: c.id for c in await store.readable_communities(db, audience, membership.user_id)}
    await store.save_preference(
        db,
        membership.user_id,
        org_id,
        level=body.level,
        weekly_digest=body.weekly_digest,
        muted_community_ids=[readable[u] for u in body.muted_community_uuids if u in readable],
        muted_labels=body.muted_labels,
        assignment_updates=body.assignment_updates,
        assignment_reminders=body.assignment_reminders,
        assignment_reminder_days=body.assignment_reminder_days,
    )
    return await _preferences_out(db, membership.user_id, org_id, membership.role_id)


def audience_days(settings) -> int:
    return effective_preference(None, settings).reminder_days


def _settings_out(settings) -> SettingsOut:
    config = get_config()
    return SettingsOut(
        instance_enabled=config.enabled,
        enabled=settings.enabled,
        default_level=settings.default_level,
        digest_enabled=settings.digest_enabled,
        digest_weekday=settings.digest_weekday,
        digest_hour=settings.digest_hour,
        timezone=settings.timezone,
        assignments_enabled=settings.assignments_enabled,
        assignment_reminder_days=audience_days(settings),  # type: ignore[arg-type]
        last_digest_at=settings.last_digest_at,
        delay_minutes=config.delay_minutes,
    )


@router.get("/org/{org_id}/settings", response_model=SettingsOut)
async def api_get_settings(
    org_id: int,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SettingsOut:
    """The organization's community email settings. Admins and maintainers."""
    await _staff(db, current_user, org_id)
    return _settings_out(await get_settings(db, org_id))


@router.put("/org/{org_id}/settings", response_model=SettingsOut)
async def api_put_settings(
    org_id: int,
    body: SettingsIn,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> SettingsOut:
    await _staff(db, current_user, org_id)
    saved = await store.save_settings(db, org_id, **body.model_dump())
    return _settings_out(saved)


def _sample_entries() -> list:
    return [
        emails.Entry(
            reason=REASON_STAFF,
            community_uuid="community_sample",
            community_name="General",
            discussion_uuid="discussion_sample",
            title="Welcome to the community",
            author="Alex Morgan",
            snippet="This is a sample post. A real email shows the newest post or comment here.",
            count=3,
        )
    ]


@router.post("/org/{org_id}/test", response_model=TestOut)
async def api_send_test(
    org_id: int,
    body: TestIn,
    current_user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> TestOut:
    """Send the caller one notification email, to their own address only.

    Uses this week's real discussions when there are any the caller can read,
    otherwise a sample post. It ignores the caller's own level, so an admin
    with email off can still check delivery.
    """
    membership = await _staff(db, current_user, org_id)
    allowed, _count, retry_after = check_rate_limit(
        f"notification_test_email:{membership.user_id}", TESTS_PER_WINDOW, TEST_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMITED", "message": "Too many test emails. Try again in a few minutes.", "retry_after": retry_after},
        )
    user = await db.get(User, membership.user_id)
    look = await load_org_look(db, org_id)
    if user is None or not user.email or look is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Your account has no email address")

    audience = await load_org_audience(db, org_id)
    threads = await week_threads(db, org_id, datetime.now())
    authors = await load_authors(db, threads)
    built = await build_for_member(db, audience, threads, user.id, authors) if threads else None
    entries, new_posts, new_comments = built or (_sample_entries(), 1, 3)

    common = dict(
        email=user.email,
        username=user.username,
        org_name=look.branding.org_name,
        base_url=look.base_url,
        one_click_url=emails.unsubscribe_url(look.base_url, user.user_uuid, org_id),
        **look.branding.as_kwargs(),
    )
    if body.kind == "assignment":
        sample = emails.AssignmentEntry(
            kind=emails.KIND_REMINDER,
            title="Sample assignment",
            course_name="Sample course",
            due_text="tomorrow",
            url=f"{look.base_url}/calendar",
        )
        sent = await asyncio.to_thread(emails.send_assignment_email, entries=[sample], **common)
    elif body.kind == "digest":
        sent = await asyncio.to_thread(
            emails.send_digest_email, entries=entries, new_posts=new_posts, new_comments=new_comments, **common
        )
    else:
        first = entries[0]
        first.reason, first.count = REASON_STAFF, 1
        sent = await asyncio.to_thread(emails.send_instant_email, entries=[first], **common)
    if sent is False:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The email could not be sent. Check the email settings of this LearnHouse instance.",
        )
    logger.info("Notification test email (%s) sent by user %s", body.kind, user.id)
    return TestOut(sent_to=user.email)


async def _unsubscribe_target(db: AsyncSession, token: str) -> Optional[tuple]:
    """``(user, org)`` named by a valid token whose user still belongs to
    the organization, else None."""
    parsed = emails.read_token(token)
    if parsed is None:
        return None
    user_uuid, org_id = parsed
    user = await get_user_by_uuid(db, user_uuid)
    org = await db.get(Organization, org_id)
    if user is None or user.id is None or org is None:
        return None
    return user, org


@router.get("/unsubscribe", response_class=HTMLResponse)
async def api_unsubscribe_page(
    token: str = Query(..., max_length=512),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """A confirmation page. It changes nothing: mail scanners open every
    link in a message, and that must not unsubscribe anyone."""
    target = await _unsubscribe_target(db, token)
    if target is None:
        return _expired_page()
    user, org = target
    form_html = (
        '<form method="post" action="unsubscribe">'
        f'<input type="hidden" name="token" value="{html.escape(token)}" />'
        f'<button type="submit" style="{_BUTTON_STYLE}">Turn off these emails</button>'
        "</form>"
    )
    return _page(
        "Turn off email notifications?",
        f"You'll stop receiving community and assignment emails from <strong>{html.escape(org.name)}</strong> "
        f"at <strong>{html.escape(str(user.email))}</strong>, the weekly digest included. Account emails "
        "like password resets will still reach you. You can turn notifications back on from your profile menu.",
        form_html,
    )


@router.post("/unsubscribe", response_class=HTMLResponse)
async def api_unsubscribe(
    token: Optional[str] = Form(default=None, max_length=512),
    token_query: Optional[str] = Query(default=None, alias="token", max_length=512),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    """Turn notification email off for the token's member. Idempotent. Also the
    one-click target named by ``List-Unsubscribe-Post``, which posts without a
    form body, so the token is read from the body or the query string."""
    target = await _unsubscribe_target(db, token or token_query or "")
    if target is None:
        return _expired_page()
    user, org = target
    await store.turn_off(db, user.id, org.id)
    logger.info("Notification emails turned off by link for user %s in organization %s", user.id, org.id)
    return _page(
        "Email notifications are off",
        f"You won't receive community or assignment emails from <strong>{html.escape(org.name)}</strong>. "
        "You can turn them back on from your profile menu.",
    )
