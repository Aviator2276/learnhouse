"""Fixtures for the community email tests: a real SQLite database, real
email rendering, and a recorder in place of the mail provider."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Optional

import pytest

from src.db.communities.communities import Community
from src.db.communities.discussion_comments import DiscussionComment
from src.db.communities.discussions import Discussion
from src.db.email_notifications import (  # noqa: F401  (registers the tables)
    EmailNotificationCursor,
    EmailNotificationPreference,
    EmailNotificationSettings,
)
from src.db.roles import Role, RoleTypeEnum
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.email_notifications import delivery, emails

BASE = "https://learn.example.com"
NOW = datetime(2026, 11, 16, 12, 0, 0)

ADMIN, MAINTAINER, INSTRUCTOR, LEARNER = 1, 2, 3, 4


class Outbox:
    """Stands in for the mail provider and keeps what it was asked to send."""

    def __init__(self) -> None:
        self.sent: list = []
        self.result: object = "id-1"
        self.raises: Optional[Exception] = None

    def __call__(self, **kwargs):
        if self.raises is not None:
            raise self.raises
        self.sent.append(kwargs)
        return self.result

    def to(self, email: str) -> list:
        return [m for m in self.sent if m["to"] == email]

    @property
    def recipients(self) -> set:
        return {m["to"] for m in self.sent}


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(emails, "_send_notification_email", box)

    async def base_url(slug, db=None, org_id=None):
        return BASE

    monkeypatch.setattr(delivery, "org_base_url", base_url)
    monkeypatch.delenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED", raising=False)
    monkeypatch.delenv("LEARNHOUSE_COMMUNITY_EMAILS_DELAY_MINUTES", raising=False)
    return box


async def add_role(db, role_id: int, name: str, rights: dict) -> None:
    db.add(Role(id=role_id, name=name, org_id=None, role_type=RoleTypeEnum.TYPE_GLOBAL,
                role_uuid=f"role_{name.lower()}", rights=rights,
                creation_date=str(NOW), update_date=str(NOW)))
    await db.commit()


@pytest.fixture
async def roles(db, org):
    """The four roles LearnHouse seeds, reduced to the rights this feature reads."""
    await add_role(db, ADMIN, "Admin", {"courses": {"action_create": True}, "communities": {"action_update": True}})
    await add_role(db, MAINTAINER, "Maintainer", {"courses": {"action_create": True}})
    await add_role(db, INSTRUCTOR, "Instructor", {"courses": {"action_create": True}, "communities": {"action_update": False}})
    await add_role(db, LEARNER, "User", {"courses": {"action_create": False}})


async def add_member(db, org_id: Optional[int], user_id: int, username: str, role_id: int = LEARNER,
                     *, verified: bool = True, email: Optional[str] = None,
                     first_name: str = "", last_name: str = "") -> PublicUser:
    user = User(
        id=user_id, username=username, first_name=first_name, last_name=last_name,
        email=f"{username}@test.com" if email is None else email, password="x",
        user_uuid=f"user_{username}", email_verified=verified,
        creation_date=str(NOW), update_date=str(NOW),
    )
    db.add(user)
    await db.commit()
    if org_id is not None:
        db.add(UserOrganization(user_id=user_id, org_id=org_id, role_id=role_id,
                                creation_date=str(NOW), update_date=str(NOW)))
        await db.commit()
    return PublicUser(id=user_id, username=username, first_name=first_name, last_name=last_name,
                      email=user.email or "nobody@test.com", user_uuid=user.user_uuid)


async def add_community(db, org_id: int, community_id: int, name: str = "General", *,
                        public: bool = False, course_id: Optional[int] = None) -> Community:
    community = Community(id=community_id, org_id=org_id, name=name, public=public, course_id=course_id,
                          community_uuid=f"community_{community_id}",
                          creation_date=str(NOW), update_date=str(NOW))
    db.add(community)
    await db.commit()
    return community


def doc(text: str) -> str:
    """Post content the way the editor stores it."""
    return json.dumps({"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]})


async def add_discussion(db, community: Community, author_id: int, title: str = "Hello", text: str = "First post",
                         *, label: str = "general", minutes_ago: float = 30, created: Optional[str] = None,
                         upvotes: int = 1, pinned: bool = False, emoji: Optional[str] = None) -> Discussion:
    discussion = Discussion(
        title=title, content=doc(text), label=label, emoji=emoji, community_id=community.id,
        org_id=community.org_id, author_id=author_id, discussion_uuid="",
        upvote_count=upvotes, is_pinned=pinned,
        creation_date=str(NOW - timedelta(minutes=minutes_ago)) if created is None else created,
        update_date=str(NOW),
    )
    db.add(discussion)
    await db.commit()
    await db.refresh(discussion)
    discussion.discussion_uuid = f"discussion_{discussion.id}"
    db.add(discussion)
    await db.commit()
    return discussion


async def add_comment(db, discussion: Discussion, author_id: int, text: str = "A reply", *,
                      minutes_ago: float = 20, created: Optional[str] = None) -> DiscussionComment:
    comment = DiscussionComment(
        content=doc(text), discussion_id=discussion.id, author_id=author_id, comment_uuid="",
        creation_date=str(NOW - timedelta(minutes=minutes_ago)) if created is None else created,
        update_date=str(NOW),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def set_pref(db, user_id: int, org_id: int = 1, **values) -> None:
    db.add(EmailNotificationPreference(user_id=user_id, org_id=org_id, **values))
    await db.commit()


async def set_settings(db, org_id: int = 1, **values) -> EmailNotificationSettings:
    row = EmailNotificationSettings(org_id=org_id, **values)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.fixture
async def school(db, org, roles):
    """An organization with staff, three learners and one open community."""
    people = SimpleNamespace(
        admin=await add_member(db, org.id, 1, "ada", ADMIN, first_name="Ada", last_name="Lovelace"),
        teacher=await add_member(db, org.id, 2, "tess", INSTRUCTOR),
        sam=await add_member(db, org.id, 3, "sam"),
        kim=await add_member(db, org.id, 4, "kim"),
        lee=await add_member(db, org.id, 5, "lee"),
    )
    people.general = await add_community(db, org.id, 1, "General")
    return people
