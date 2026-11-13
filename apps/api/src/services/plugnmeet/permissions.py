"""Who may see, join, schedule and run a live class.

Only signed-in members of the organization ever see or join a class: no
guests, no anonymous users, no API tokens, no members of other orgs.

* Organization-wide class: any member may join; org admins and maintainers
  moderate and schedule.
* Course class: members who can read the course may join; whoever can edit
  the course (its authors/contributors, org admins, maintainers) moderates
  and schedules. Course access is LearnHouse's own RBAC, not a copy of it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, APITokenUser
from src.security.rbac import AccessAction, check_resource_access
from src.security.rbac.constants import is_admin_or_maintainer


def require_signed_in(current_user: Any) -> int:
    """The user id of a real signed-in user, or 401."""
    if (
        current_user is None
        or isinstance(current_user, (AnonymousUser, APITokenUser))
        or not getattr(current_user, "id", None)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to use live classes")
    return int(current_user.id)


@dataclass
class OrgAccess:
    """The caller's standing in one organization, with memoized course checks
    so listing many classes of the same course does not repeat RBAC queries."""

    request: Request
    db: AsyncSession
    current_user: Any
    user_id: int
    org_id: int
    role_id: Optional[int]
    _read: dict[int, bool] = field(default_factory=dict)
    _edit: dict[int, bool] = field(default_factory=dict)

    @property
    def is_member(self) -> bool:
        return self.role_id is not None

    @property
    def is_staff(self) -> bool:
        return self.role_id is not None and is_admin_or_maintainer(self.role_id)

    async def _course_check(self, course: Course, action: AccessAction, memo: dict[int, bool]) -> bool:
        if course.org_id != self.org_id or not self.is_member:
            return False
        assert course.id is not None
        if course.id not in memo:
            decision = await check_resource_access(
                self.request, self.db, self.current_user, course.course_uuid, action, raise_on_deny=False
            )
            memo[course.id] = bool(decision.allowed)
        return memo[course.id]

    async def can_read_course(self, course: Course) -> bool:
        return await self._course_check(course, AccessAction.READ, self._read)

    async def can_edit_course(self, course: Course) -> bool:
        return await self._course_check(course, AccessAction.UPDATE, self._edit)

    async def can_view(self, course: Optional[Course]) -> bool:
        if not self.is_member:
            return False
        if course is None:
            return True
        return await self.can_edit_course(course) or await self.can_read_course(course)

    async def can_moderate(self, course: Optional[Course]) -> bool:
        if not self.is_member:
            return False
        if course is None:
            return self.is_staff
        return self.is_staff or await self.can_edit_course(course)


async def org_access(request: Request, db: AsyncSession, current_user: Any, org_id: int) -> OrgAccess:
    user_id = require_signed_in(current_user)
    membership = (await db.execute(
        select(UserOrganization).where(UserOrganization.user_id == user_id, UserOrganization.org_id == org_id)
    )).scalars().first()
    return OrgAccess(
        request=request,
        db=db,
        current_user=current_user,
        user_id=user_id,
        org_id=org_id,
        role_id=membership.role_id if membership else None,
    )


def require_member(access: OrgAccess) -> None:
    if not access.is_member:
        # 404, not 403: a non-member learns nothing about the org's classes.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Live class not found")
