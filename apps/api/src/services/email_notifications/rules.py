"""Who wants which email. Pure functions, no database."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.db.communities.discussions import DISCUSSION_LABELS
from src.services.communities.moderation import parse_content_for_moderation

LEVEL_OFF = "off"
LEVEL_MENTIONS = "mentions"
LEVEL_REPLIES = "replies"
LEVEL_HIGHLIGHTS = "highlights"
LEVEL_ALL = "all"
LEVELS = (LEVEL_OFF, LEVEL_MENTIONS, LEVEL_REPLIES, LEVEL_HIGHLIGHTS, LEVEL_ALL)
DEFAULT_LEVEL = LEVEL_HIGHLIGHTS

# Why a member is told about a post, strongest first.
REASON_MENTION = "mention"
REASON_REPLY = "reply"
REASON_STAFF = "staff"
REASON_NEW = "new"
REASON_RANK = {REASON_MENTION: 0, REASON_REPLY: 1, REASON_STAFF: 2, REASON_NEW: 3}

_LEVELS_FOR_REASON = {
    REASON_MENTION: frozenset({LEVEL_MENTIONS, LEVEL_REPLIES, LEVEL_HIGHLIGHTS, LEVEL_ALL}),
    REASON_REPLY: frozenset({LEVEL_REPLIES, LEVEL_HIGHLIGHTS, LEVEL_ALL}),
    REASON_STAFF: frozenset({LEVEL_HIGHLIGHTS, LEVEL_ALL}),
    REASON_NEW: frozenset({LEVEL_ALL}),
}

REMINDER_DAY_CHOICES = (1, 2, 3, 7)
DEFAULT_REMINDER_DAYS = 1

LABEL_IDS = tuple(label["id"] for label in DISCUSSION_LABELS)

# "@name" that is not part of an email address or a URL.
_MENTION = re.compile(r"(?<![A-Za-z0-9_@./-])@([A-Za-z0-9][A-Za-z0-9_.-]{0,63})")


@dataclass(frozen=True)
class Preference:
    """A member's effective choices in one organization."""

    level: str = DEFAULT_LEVEL
    weekly_digest: bool = True
    muted_communities: frozenset = frozenset()
    muted_labels: frozenset = frozenset()
    assignment_updates: bool = True
    assignment_reminders: bool = True
    reminder_days: int = 1


def wants(pref: Preference, reason: str, community_id: int, label: Optional[str]) -> bool:
    """Whether a post reaches this member as an instant email.

    A muted community is silent, mentions included. A muted post type only
    hides posts the member has no personal stake in: a mention or a reply in
    their own discussion still gets through.
    """
    if community_id in pref.muted_communities:
        return False
    if reason in (REASON_STAFF, REASON_NEW) and (label or "general") in pref.muted_labels:
        return False
    return pref.level in _LEVELS_FOR_REASON[reason]


def wants_digest(pref: Preference) -> bool:
    return pref.level != LEVEL_OFF and pref.weekly_digest


def plain_text(content: Optional[str]) -> str:
    """Post content (tiptap JSON or legacy plain text) as one line of text."""
    return " ".join(parse_content_for_moderation(content or "").split())


def mentioned_usernames(text: str) -> set:
    """Lowercased usernames written as ``@name`` in ``text``.

    A sentence can end right after a name ("thanks @sam."), so the name is
    also offered without its trailing punctuation.
    """
    found = set()
    for match in _MENTION.finditer(text or ""):
        name = match.group(1).lower()
        found.add(name)
        found.add(name.rstrip("._-"))
    found.discard("")
    return found


def parse_created(value: Optional[str]) -> Optional[datetime]:
    """A row's ``creation_date`` as naive local time, the form upstream writes."""
    try:
        parsed = datetime.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed
