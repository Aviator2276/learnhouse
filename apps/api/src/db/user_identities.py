from typing import Optional

from sqlalchemy import Column, ForeignKey, Index, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


class UserExternalIdentity(SQLModel, table=True):
    """A LearnHouse account's link to an external OpenID Connect identity.

    Accounts are matched on the immutable ``(provider, issuer, subject)``
    triple, never on email: an email address can change or be recycled at the
    IdP, a ``sub`` cannot. Email is only used once, to link an existing
    account the first time, and only when the IdP marks it verified.
    """

    __tablename__ = "userexternalidentity"
    __table_args__ = (
        UniqueConstraint("provider", "issuer", "subject", name="uq_user_ext_identity_subject"),
        # One identity per provider/issuer per account, so a second IdP account
        # can never be attached to an already-linked LearnHouse account.
        UniqueConstraint("user_id", "provider", "issuer", name="uq_user_ext_identity_user"),
        Index("ix_user_ext_identity_user", "user_id"),
        {"extend_existing": True},
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    provider: str = Field(max_length=32)
    issuer: str = Field(max_length=512)
    subject: str = Field(max_length=255)
    email_at_link: str = ""
    creation_date: str = ""
    last_login_at: str = ""
