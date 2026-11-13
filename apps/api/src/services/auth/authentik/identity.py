"""Turn validated Authentik claims into a LearnHouse account and membership.

Security rules, in order:

1. An account is found by its ``(provider, issuer, sub)`` link. Email is never
   the lookup key for a linked account, so an email change or a recycled
   address at the IdP cannot move someone into another person's account.
2. With no link yet, an existing account is linked by email only when the IdP
   says the email is verified, the operator allows email linking, and the
   account is not already linked to a *different* Authentik subject.
3. Linking applies the same pre-hijack guard as the Google path: a password
   set by someone who never proved the inbox is dropped and its sessions end.
4. Joining an org follows the org's own join rules (open / invite-only),
   checked against the IdP-verified identity, never the request body.
5. Group -> role sync never demotes an org's last admin.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.user_identities import UserExternalIdentity
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, User, UserCreate
from src.services.auth.authentik.config import ROLE_PRIORITY, AuthentikSettings

logger = logging.getLogger(__name__)

PROVIDER = "authentik"
_USERNAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def claim_groups(claims: Dict[str, Any]) -> frozenset[str]:
    groups = claims.get("groups")
    if not isinstance(groups, list):
        return frozenset()
    return frozenset(g for g in groups if isinstance(g, str) and g and len(g) <= 256)


def check_allowed_groups(settings: AuthentikSettings, groups: frozenset[str]) -> None:
    if settings.allowed_groups and not (settings.allowed_groups & groups):
        raise _forbidden("Your account is not allowed to sign in to LearnHouse.")


async def find_linked_user(db: AsyncSession, issuer: str, subject: str) -> Optional[User]:
    link = (
        await db.execute(
            select(UserExternalIdentity).where(
                (UserExternalIdentity.provider == PROVIDER)
                & (UserExternalIdentity.issuer == issuer)
                & (UserExternalIdentity.subject == subject)
            )
        )
    ).scalars().first()
    if link is None:
        return None
    return await db.get(User, link.user_id)


async def find_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    return (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalars().first()


async def _existing_link_for_user(db: AsyncSession, user_id: int, issuer: str) -> Optional[UserExternalIdentity]:
    return (
        await db.execute(
            select(UserExternalIdentity).where(
                (UserExternalIdentity.user_id == user_id)
                & (UserExternalIdentity.provider == PROVIDER)
                & (UserExternalIdentity.issuer == issuer)
            )
        )
    ).scalars().first()


async def link_identity(db: AsyncSession, user: User, issuer: str, subject: str, email: str) -> None:
    db.add(
        UserExternalIdentity(
            user_id=user.id,
            provider=PROVIDER,
            issuer=issuer,
            subject=subject,
            email_at_link=email,
            creation_date=_now(),
            last_login_at=_now(),
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # Lost a race with a concurrent first login, or the account was linked
        # to another subject in between. Either way: refuse, never merge.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account is already linked to a different Authentik identity.",
        ) from exc


async def touch_identity(db: AsyncSession, issuer: str, subject: str) -> None:
    link = (
        await db.execute(
            select(UserExternalIdentity).where(
                (UserExternalIdentity.provider == PROVIDER)
                & (UserExternalIdentity.issuer == issuer)
                & (UserExternalIdentity.subject == subject)
            )
        )
    ).scalars().first()
    if link is not None:
        link.last_login_at = _now()
        db.add(link)
        await db.commit()


async def apply_prehijack_guard(db: AsyncSession, user: User) -> None:
    """Same rule as ``signWithGoogle``: anything set on the account before its
    owner proved the inbox may have been set by someone else."""
    untrusted_password = bool(user.password) and (
        not user.email_verified
        or (user.signup_method == "admin_api" and user.password_changed_at is None)
    )
    changed = False
    if untrusted_password:
        user.password = ""
        user.password_changed_at = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        changed = True
    if not user.email_verified:
        user.email_verified = True
        user.email_verified_at = _now()
        changed = True
    if not user.signup_method:
        user.signup_method = PROVIDER
        changed = True
    if changed:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    if untrusted_password and user.id is not None:
        from src.security.auth import revoke_user_sessions_before

        revoke_user_sessions_before(user.id)


def _username_from_claims(claims: Dict[str, Any], email: str) -> str:
    base = claims.get("preferred_username") or claims.get("nickname") or email.split("@")[0]
    base = _USERNAME_SAFE.sub("", str(base))[:32] or "user"
    return f"{base}{secrets.randbelow(900000) + 100000}"


def _name_part(claims: Dict[str, Any], key: str) -> str:
    value = claims.get(key)
    return value.strip()[:100] if isinstance(value, str) else ""


async def provision_user(
    request: Request, db: AsyncSession, claims: Dict[str, Any], email: str, org_id: Optional[int]
) -> User:
    from src.services.users.users import create_user, create_user_without_org

    first, last = _name_part(claims, "given_name"), _name_part(claims, "family_name")
    if not first and not last:
        name = _name_part(claims, "name")
        first, _, last = name.partition(" ")
    user_object = UserCreate(
        email=email,
        username=_username_from_claims(claims, email),
        password="",
        first_name=first,
        last_name=last,
    )
    if org_id is not None:
        created = await create_user(
            request, db, AnonymousUser(), user_object, org_id, is_oauth=True, signup_provider=PROVIDER
        )
    else:
        created = await create_user_without_org(
            request, db, AnonymousUser(), user_object, is_oauth=True, signup_provider=PROVIDER
        )
    user = await db.get(User, created.id)
    assert user is not None
    return user


async def is_member(db: AsyncSession, user_id: int, org_id: int) -> bool:
    return (
        await db.execute(
            select(UserOrganization).where(
                (UserOrganization.user_id == user_id) & (UserOrganization.org_id == org_id)
            )
        )
    ).scalars().first() is not None


class OrgAdmission:
    """Outcome of the org join gate."""

    def __init__(self, usergroup_id: Optional[int] = None, consume_invite_key: Optional[str] = None):
        self.usergroup_id = usergroup_id
        self.consume_invite_key = consume_invite_key


async def admit_to_org(
    request: Request,
    db: AsyncSession,
    settings: AuthentikSettings,
    org: Organization,
    email: str,
    existing_user: Optional[User],
    invite_code: Optional[str],
) -> OrgAdmission:
    """Mirror of the Google path's org gate. Raises 403 when the verified
    identity may not join ``org``."""
    from src.services.orgs.invites import get_invite_code
    from src.services.orgs.orgs import get_org_join_mechanism

    mechanism = await get_org_join_mechanism(request, org.id, AnonymousUser(), db)
    if mechanism != "inviteOnly":
        return OrgAdmission()
    if existing_user is not None and await is_member(db, existing_user.id, org.id):
        return OrgAdmission()
    if settings.bypass_invite:
        return OrgAdmission()

    if invite_code:
        try:
            code_data = await get_invite_code(request, org.id, invite_code, AnonymousUser(), db)
        except HTTPException:
            code_data = None
        if code_data:
            return OrgAdmission(usergroup_id=code_data.get("usergroup_id"))

    from src.core.redis import get_redis_client

    r = get_redis_client()
    if r is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify your invitation right now, please try again.",
        )
    key = f"invited_user:{email}:org:{org.org_uuid}"
    try:
        found = r.get(key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify your invitation right now, please try again.",
        ) from exc
    if found:
        return OrgAdmission(consume_invite_key=key)
    raise _forbidden("You need an invite to join this organization")


async def join_org(request: Request, db: AsyncSession, user: User, org_id: int) -> bool:
    """Add an existing account to ``org_id``. Returns True when newly joined."""
    if await is_member(db, user.id, org_id):
        return False
    from src.security.features_utils.usage import check_limits_with_usage, increase_feature_usage

    await check_limits_with_usage("members", org_id, db)
    db.add(
        UserOrganization(
            user_id=user.id,
            org_id=org_id,
            role_id=4,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
    )
    await db.commit()
    await increase_feature_usage("members", org_id, db)
    _invalidate_session_cache(user.id)

    from src.services.analytics import events as analytics_events
    from src.services.analytics.analytics import track
    from src.services.orgs.join_notifications import notify_user_joined_org

    await track(
        event_name=analytics_events.USER_JOINED_ORG,
        org_id=org_id,
        user_id=user.id,
        properties={"join_method": PROVIDER},
    )
    await notify_user_joined_org(request, db, user, org_id)
    return True


def _invalidate_session_cache(user_id: int) -> None:
    from src.routers.users import _invalidate_session_cache as invalidate

    invalidate(user_id)


async def finish_admission(request: Request, db: AsyncSession, user: User, admission: OrgAdmission) -> None:
    """Apply the invite's usergroup and mark an emailed invite accepted. Best
    effort: the user is already in the org, a failure here must not undo it."""
    if admission.usergroup_id:
        from src.db.users import InternalUser
        from src.services.users.usergroups import add_users_to_usergroup

        try:
            await add_users_to_usergroup(
                request, db, InternalUser(id=0), int(admission.usergroup_id), str(user.id)
            )
        except Exception:
            logger.warning("Could not attach Authentik user to invite usergroup")
    if admission.consume_invite_key:
        from src.core.redis import get_redis_client

        try:
            r = get_redis_client()
            raw = r.get(admission.consume_invite_key) if r is not None else None
            if raw:
                record = json.loads(raw)
                record["pending"] = False
                ttl = r.ttl(admission.consume_invite_key)
                r.set(admission.consume_invite_key, json.dumps(record), ex=ttl if ttl > 0 else None)
        except Exception:
            logger.warning("Could not mark invitation as accepted")


def desired_role_uuid(settings: AuthentikSettings, groups: Iterable[str]) -> Optional[str]:
    """Most privileged role the user's groups map to, or ``None`` when the
    mapping is off. A user in no mapped group gets the plain user role."""
    if not settings.role_mapping or not settings.role_sync:
        return None
    mapped = {settings.role_mapping[g] for g in groups if g in settings.role_mapping}
    for role_uuid in ROLE_PRIORITY:
        if role_uuid in mapped:
            return role_uuid
    return "role_global_user"


async def sync_org_role(db: AsyncSession, settings: AuthentikSettings, user: User, org_id: int, groups: frozenset[str]) -> None:
    target_uuid = desired_role_uuid(settings, groups)
    if target_uuid is None:
        return
    membership = (
        await db.execute(
            select(UserOrganization).where(
                (UserOrganization.user_id == user.id) & (UserOrganization.org_id == org_id)
            )
        )
    ).scalars().first()
    if membership is None:
        return
    target = (
        await db.execute(select(Role).where((Role.role_uuid == target_uuid) & (Role.role_type == RoleTypeEnum.TYPE_GLOBAL)))
    ).scalars().first()
    if target is None or target.id == membership.role_id:
        return

    admin_role = (
        await db.execute(select(Role).where((Role.role_uuid == "role_global_admin") & (Role.role_type == RoleTypeEnum.TYPE_GLOBAL)))
    ).scalars().first()
    if admin_role is not None and membership.role_id == admin_role.id and target.id != admin_role.id:
        admins = (
            await db.execute(
                select(func.count()).select_from(UserOrganization).where(
                    (UserOrganization.org_id == org_id) & (UserOrganization.role_id == admin_role.id)
                )
            )
        ).scalar_one()
        if admins <= 1:
            logger.warning(
                "Authentik role sync left user %s as admin of org %s: they are its last admin",
                user.id,
                org_id,
            )
            return

    membership.role_id = target.id
    membership.update_date = str(datetime.now())
    db.add(membership)
    await db.commit()
    _invalidate_session_cache(user.id)
    logger.info("Authentik role sync set user %s in org %s to %s", user.id, org_id, target_uuid)
