"""Who can read a community, who counts as an instructor, and what each
member chose. Loaded once per organization and run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.communities.communities import Community
from src.db.email_notifications import EmailNotificationPreference, EmailNotificationSettings
from src.db.courses.courses import Course
from src.db.resource_authors import ResourceAuthor, ResourceAuthorshipEnum, ResourceAuthorshipStatusEnum
from src.db.roles import Role
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS
from src.services.email_notifications.rules import (
    DEFAULT_LEVEL,
    DEFAULT_REMINDER_DAYS,
    LEVELS,
    REMINDER_DAY_CHOICES,
    Preference,
)

_AUTHOR_KINDS = (
    ResourceAuthorshipEnum.CREATOR,
    ResourceAuthorshipEnum.CONTRIBUTOR,
    ResourceAuthorshipEnum.MAINTAINER,
)


def _grants(rights: Any, section: str, action: str) -> bool:
    if not isinstance(rights, dict):
        return False
    block = rights.get(section)
    return isinstance(block, dict) and block.get(action) is True


def default_settings(org_id: int) -> EmailNotificationSettings:
    return EmailNotificationSettings(org_id=org_id)


async def get_settings(db: AsyncSession, org_id: int) -> EmailNotificationSettings:
    """The organization's settings, or unsaved defaults when it has none."""
    row = (await db.execute(
        select(EmailNotificationSettings).where(EmailNotificationSettings.org_id == org_id)
    )).scalars().first()
    return row or default_settings(org_id)


def effective_preference(
    row: Optional[EmailNotificationPreference], settings: EmailNotificationSettings
) -> Preference:
    default = settings.default_level if settings.default_level in LEVELS else DEFAULT_LEVEL
    days = (
        settings.assignment_reminder_days
        if settings.assignment_reminder_days in REMINDER_DAY_CHOICES
        else DEFAULT_REMINDER_DAYS
    )
    if row is None:
        return Preference(level=default, reminder_days=days)
    return Preference(
        level=row.level if row.level in LEVELS else default,
        weekly_digest=bool(row.weekly_digest),
        muted_communities=frozenset(int(i) for i in (row.muted_community_ids or [])),
        muted_labels=frozenset(str(label) for label in (row.muted_labels or [])),
        assignment_updates=bool(row.assignment_updates),
        assignment_reminders=bool(row.assignment_reminders),
        reminder_days=row.assignment_reminder_days if row.assignment_reminder_days in REMINDER_DAY_CHOICES else days,
    )


@dataclass
class OrgAudience:
    """One organization's members, as far as community email is concerned."""

    org_id: int
    settings: EmailNotificationSettings
    # user id -> role id
    members: dict
    # Instructors by role: admins, maintainers, and roles that may create courses.
    staff: set
    # May read every community: admins, maintainers, community managers.
    managers: set
    preferences: dict
    _readers: dict = field(default_factory=dict)
    _course_authors: dict = field(default_factory=dict)

    def preference(self, user_id: int) -> Preference:
        return effective_preference(self.preferences.get(user_id), self.settings)

    async def readers(self, db: AsyncSession, community: Community) -> set:
        """Members who may read ``community``.

        The same rule as the community list: a public community and one not
        tied to any user group are open to every member; one tied to user
        groups is open to those groups and to managers. People outside the
        organization are never included, public community or not.
        """
        assert community.id is not None
        if community.id not in self._readers:
            everyone = set(self.members)
            if community.public:
                allowed = everyone
            else:
                group_ids = (await db.execute(
                    select(UserGroupResource.usergroup_id).where(
                        UserGroupResource.resource_uuid == community.community_uuid
                    )
                )).scalars().all()
                if not group_ids:
                    allowed = everyone
                else:
                    in_groups = (await db.execute(
                        select(UserGroupUser.user_id).where(UserGroupUser.usergroup_id.in_(group_ids))  # type: ignore[attr-defined]
                    )).scalars().all()
                    allowed = (set(in_groups) | self.managers) & everyone
            self._readers[community.id] = allowed
        return self._readers[community.id]

    async def instructors(self, db: AsyncSession, community: Community) -> set:
        """Staff by role, plus the authors of the community's course."""
        if community.course_id is None:
            return self.staff
        if community.course_id not in self._course_authors:
            course = await db.get(Course, community.course_id)
            authors: set = set()
            if course is not None:
                authors = set((await db.execute(
                    select(ResourceAuthor.user_id).where(
                        ResourceAuthor.resource_uuid == course.course_uuid,
                        ResourceAuthor.authorship_status == ResourceAuthorshipStatusEnum.ACTIVE,
                        ResourceAuthor.authorship.in_(_AUTHOR_KINDS),  # type: ignore[attr-defined]
                    )
                )).scalars().all())
            self._course_authors[community.course_id] = authors & set(self.members)
        return self.staff | self._course_authors[community.course_id]


async def load_org_audience(db: AsyncSession, org_id: int) -> OrgAudience:
    memberships = (await db.execute(
        select(UserOrganization.user_id, UserOrganization.role_id).where(UserOrganization.org_id == org_id)
    )).all()
    members = {user_id: role_id for user_id, role_id in memberships}

    role_ids = {role_id for role_id in members.values() if role_id is not None}
    roles = (await db.execute(select(Role).where(Role.id.in_(role_ids)))).scalars().all() if role_ids else []  # type: ignore[union-attr]
    staff_roles = set(ADMIN_OR_MAINTAINER_ROLE_IDS)
    manager_roles = set(ADMIN_OR_MAINTAINER_ROLE_IDS)
    for role in roles:
        if _grants(role.rights, "courses", "action_create"):
            staff_roles.add(role.id)
        if _grants(role.rights, "communities", "action_update"):
            manager_roles.add(role.id)

    prefs = (await db.execute(
        select(EmailNotificationPreference).where(EmailNotificationPreference.org_id == org_id)
    )).scalars().all()

    return OrgAudience(
        org_id=org_id,
        settings=await get_settings(db, org_id),
        members=members,
        staff={uid for uid, rid in members.items() if rid in staff_roles},
        managers={uid for uid, rid in members.items() if rid in manager_roles},
        preferences={p.user_id: p for p in prefs},
    )
