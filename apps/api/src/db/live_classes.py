"""Live classes (plugNmeet) and their attendance. Fork-only tables."""

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String
from sqlmodel import Field, SQLModel


class LiveClass(SQLModel, table=True):
    """One scheduled occurrence. A recurring series is several rows sharing
    ``series_uuid``, so each occurrence can be moved or cancelled on its own.

    ``course_id`` NULL means an organization-wide class (any member may join).
    ``starts_at``/``ends_at`` are UTC; ``timezone`` is the IANA zone the
    schedule was written in, kept so a series repeats at the same wall-clock
    time across daylight-saving changes.
    """

    __tablename__ = "liveclass"
    __table_args__ = (
        Index("ix_liveclass_org_starts", "org_id", "starts_at"),
        Index("ix_liveclass_org_ends", "org_id", "ends_at"),
        Index("ix_liveclass_course", "course_id"),
        Index("ix_liveclass_series", "series_uuid"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    class_uuid: str = Field(sa_column=Column(String(64), nullable=False, unique=True))
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="CASCADE"), nullable=False)
    )
    course_id: Optional[int] = Field(
        default=None, sa_column=Column(Integer, ForeignKey("course.id", ondelete="CASCADE"), nullable=True)
    )
    series_uuid: Optional[str] = Field(default=None, sa_column=Column(String(64), nullable=True))
    title: str = Field(sa_column=Column(String(200), nullable=False))
    description: str = Field(default="", sa_column=Column(String(2000), nullable=False, server_default=""))
    starts_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    ends_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    timezone: str = Field(default="UTC", sa_column=Column(String(64), nullable=False, server_default="UTC"))
    cancelled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default="false"))
    # Who scheduled it, for the record. Deliberately not a foreign key: the
    # class belongs to the org and must outlive the account that created it.
    created_by_id: Optional[int] = Field(default=None, sa_column=Column(Integer, nullable=True))
    creation_date: str = ""
    update_date: str = ""


class LiveClassAttendance(SQLModel, table=True):
    """One stay in the room: joined, and left (NULL while still inside).
    Written only from verified plugNmeet webhooks."""

    __tablename__ = "liveclassattendance"
    __table_args__ = (
        Index("ix_liveclassattendance_class_user", "class_id", "user_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    class_id: int = Field(
        sa_column=Column(Integer, ForeignKey("liveclass.id", ondelete="CASCADE"), nullable=False)
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    joined_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    left_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
