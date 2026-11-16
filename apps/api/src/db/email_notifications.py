"""Email notifications for communities and assignments: org settings, member
preferences and the ledgers that keep an email from going out twice.
Fork-only tables."""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class EmailNotificationSettings(SQLModel, table=True):
    """One organization's community email settings. No row means the defaults."""

    __tablename__ = "emailnotificationsettings"
    __table_args__ = (
        UniqueConstraint("org_id", name="uq_emailnotificationsettings_org"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False)
    )
    # Off stops every community email of the organization, digest included.
    enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    # What a member gets until they choose for themselves.
    default_level: str = Field(
        default="highlights", sa_column=Column(String(20), nullable=False, server_default="highlights")
    )
    digest_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    # 0 = Monday … 6 = Sunday, in ``timezone``.
    digest_weekday: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    digest_hour: int = Field(default=8, sa_column=Column(Integer, nullable=False, server_default="8"))
    timezone: str = Field(default="UTC", sa_column=Column(String(64), nullable=False, server_default="UTC"))
    # Assignment emails: new assignments, due date changes and reminders.
    assignments_enabled: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    # How many days before the due date a reminder goes out, unless a member
    # picks their own.
    assignment_reminder_days: int = Field(default=1, sa_column=Column(Integer, nullable=False, server_default="1"))
    # Set before a digest run starts sending, so a run is never repeated.
    last_digest_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    update_date: str = Field(default="", sa_column=Column(String, nullable=False, server_default=""))


class EmailNotificationPreference(SQLModel, table=True):
    """One member's choices in one organization. No row means the org default."""

    __tablename__ = "emailnotificationpreference"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", name="uq_emailnotificationpreference_user_org"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True)
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False)
    )
    # NULL follows the organization's default level.
    level: Optional[str] = Field(default=None, sa_column=Column(String(20), nullable=True))
    weekly_digest: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    # Exclusions rather than selections, so a new community or post type is
    # included until the member turns it off.
    muted_community_ids: List[int] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    muted_labels: List[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    assignment_updates: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    assignment_reminders: bool = Field(default=True, sa_column=Column(Boolean, nullable=False, server_default="1"))
    # NULL follows the organization's reminder time.
    assignment_reminder_days: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    update_date: str = Field(default="", sa_column=Column(String, nullable=False, server_default=""))


class EmailNotificationCursor(SQLModel, table=True):
    """How far the jobs have read. One row."""

    __tablename__ = "emailnotificationcursor"
    __table_args__ = ({"extend_existing": True},)

    id: Optional[int] = Field(default=None, primary_key=True)
    last_discussion_id: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    last_comment_id: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    # False until the assignment job has taken its first snapshot.
    assignments_seeded: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="0"))


class EmailNotificationAssignmentState(SQLModel, table=True):
    """What the assignment job last saw of one assignment. Comparing it with
    the assignment as it is now shows what changed, without touching
    upstream's assignment code."""

    __tablename__ = "emailnotificationassignmentstate"
    __table_args__ = (
        UniqueConstraint("assignment_id", name="uq_emailnotificationassignmentstate_assignment"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(
        sa_column=Column(Integer, ForeignKey("assignment.id", ondelete="CASCADE"), nullable=False)
    )
    # Published, in a published activity: learners can see it.
    visible: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="0"))
    due_date: str = Field(default="", sa_column=Column(String, nullable=False, server_default=""))


class EmailNotificationReminder(SQLModel, table=True):
    """One reminder sent (or deliberately skipped) for a learner, an
    assignment and a due date. Moving the due date allows a new reminder."""

    __tablename__ = "emailnotificationreminder"
    __table_args__ = (
        UniqueConstraint("assignment_id", "user_id", "due_date", name="uq_emailnotificationreminder"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    assignment_id: int = Field(
        sa_column=Column(Integer, ForeignKey("assignment.id", ondelete="CASCADE"), nullable=False)
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    due_date: str = Field(sa_column=Column(String, nullable=False))
