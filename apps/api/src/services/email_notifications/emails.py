"""The two community emails, drawn with LearnHouse's own email layout so they
look like every other email the platform sends."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from src.services.email.safe_text import email_org_name, email_user_name
from src.services.security.profile_validation import sanitize_display_name
from src.services.nudges.tokens import make_unsubscribe_token, verify_unsubscribe_token
from src.services.users.emails import (
    COLORS,
    STYLES,
    UNREAD_LIST_LIMIT,
    _brand_logo_html,
    _button_style,
    _email_layout,
    _send_notification_email,
    _unread_row,
)
from src.services.email_notifications.rules import (
    REASON_MENTION,
    REASON_REPLY,
)

SETTINGS_PATH = "/email-notifications"
FOOTER_NOTE = "You get this email because of your email notification settings."
SETTINGS_LABEL = "Change email notification settings"


@dataclass
class Entry:
    """One discussion in an email: the newest thing that happened in it."""

    reason: str
    community_uuid: str
    community_name: str
    discussion_uuid: str
    title: str
    author: str
    snippet: str
    is_comment: bool = False
    emoji: str = ""
    count: int = 1


def _title(value: Optional[str]) -> str:
    """Link-free discussion title for subjects and previews."""
    return sanitize_display_name(value, fallback="a discussion")


def discussion_url(base: str, community_uuid: str, discussion_uuid: str) -> str:
    """The reader-facing page. The web app addresses both by bare uuid."""
    community = quote(community_uuid.replace("community_", "", 1), safe="")
    discussion = quote(discussion_uuid.replace("discussion_", "", 1), safe="")
    return f"{base}/community/{community}/discussion/{discussion}"


def _category(org_id: int) -> str:
    # The organization is part of the signature: a link from one
    # organization's email cannot switch off another's.
    return f"notifications:{org_id}"


def make_token(user_uuid: str, org_id: int) -> str:
    return f"{org_id}.{make_unsubscribe_token(user_uuid, _category(org_id))}"


def read_token(token: str) -> Optional[tuple]:
    """``(user_uuid, org_id)`` for a valid token, else None."""
    head, _, rest = (token or "").partition(".")
    if not head.isdigit() or len(head) > 12 or not rest:
        return None
    org_id = int(head)
    user_uuid = verify_unsubscribe_token(rest, _category(org_id))
    return (user_uuid, org_id) if user_uuid else None


def unsubscribe_url(base: str, user_uuid: str, org_id: int) -> str:
    return f"{base}/api/v1/email-notifications/unsubscribe?token={quote(make_token(user_uuid, org_id), safe='')}"


def _headers(one_click_url: str) -> Optional[dict]:
    if not one_click_url:
        return None
    return {
        "List-Unsubscribe": f"<{one_click_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _rows(entries: list) -> tuple:
    shown = entries[:UNREAD_LIST_LIMIT]
    rows = [
        _unread_row(
            {
                "title": entry.title,
                "author": entry.author,
                "snippet": entry.snippet,
                "count": entry.count if entry.count > 1 else 0,
                "emoji": entry.emoji,
            },
            index == len(shown) - 1,
        )
        for index, entry in enumerate(shown)
    ]
    more = len(entries) - len(shown)
    more_html = (
        f'<p style="{STYLES["footer_text"]} margin: -12px 0 24px 0;">'
        f"And {more} more discussion{'s' if more > 1 else ''}.</p>"
        if more > 0 else ""
    )
    panel = (
        f'<table role="presentation" class="lh-panel" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border: 1px solid {COLORS["border"]}; border-radius: 10px; border-collapse: separate; '
        f'margin: 0 0 24px 0;">{"".join(rows)}</table>{more_html}'
    )
    return panel, shown


def instant_heading(entries: list) -> str:
    if len(entries) != 1:
        return f"{len(entries)} discussions with new activity"
    entry = entries[0]
    author = email_user_name(entry.author)
    title = _title(entry.title)
    if entry.reason == REASON_MENTION:
        return f"{author} mentioned you in {title}"
    if entry.is_comment:
        verb = "replied in" if entry.reason == REASON_REPLY else "commented on"
        return f"{author} {verb} {title}"
    return f"{author} posted in {email_org_name(entry.community_name)}"


def send_instant_email(
    email: str,
    username: str,
    org_name: str,
    entries: list,
    base_url: str,
    one_click_url: str = "",
    lang: str = "en",
    logo_url: Optional[str] = None,
    sender_name: Optional[str] = None,
    brand_color: Optional[str] = None,
    powered_by: bool = True,
):
    """New posts and comments for one member, newest first. One email covers
    every discussion, so a busy thread never becomes a stack of emails."""
    safe_username = html.escape(email_user_name(username))
    safe_org_name = html.escape(email_org_name(org_name))
    heading = instant_heading(entries)
    panel, shown = _rows(entries)

    if len(entries) == 1:
        cta_url = discussion_url(base_url, entries[0].community_uuid, entries[0].discussion_uuid)
        cta_label = "Open discussion"
    else:
        cta_url, cta_label = f"{base_url}/communities", "Open communities"

    body_content = f"""
        <h1 style="{STYLES['h1']}">{html.escape(heading)}</h1>
        <p style="{STYLES['p']}">Hi {safe_username}, here's what's new in {safe_org_name}.</p>
        {panel}
        <a href="{html.escape(cta_url)}" style="{_button_style(brand_color)}">{cta_label}</a>
    """
    first = shown[0]
    return _send_notification_email(
        to=email,
        subject=heading if len(entries) == 1 else f"{heading} in {email_org_name(org_name)}",
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=FOOTER_NOTE,
            logo_html=_brand_logo_html(logo_url, org_name),
            unsubscribe_url=f"{base_url}{SETTINGS_PATH}",
            unsubscribe_label=SETTINGS_LABEL,
            preheader=f"{email_user_name(first.author)}: {first.snippet}"[:120],
            powered_by=powered_by,
            lang=lang,
        ),
        headers=_headers(one_click_url),
        sender_name=sender_name,
    )


def send_digest_email(
    email: str,
    username: str,
    org_name: str,
    entries: list,
    new_posts: int,
    new_comments: int,
    base_url: str,
    one_click_url: str = "",
    lang: str = "en",
    logo_url: Optional[str] = None,
    sender_name: Optional[str] = None,
    brand_color: Optional[str] = None,
    powered_by: bool = True,
):
    """The week's most active discussions for one member."""
    safe_username = html.escape(email_user_name(username))
    clean_org_name = email_org_name(org_name)
    heading = f"This week in {clean_org_name}"
    panel, shown = _rows(entries)
    posts = f"{new_posts} new post{'' if new_posts == 1 else 's'}"
    comments = f"{new_comments} comment{'' if new_comments == 1 else 's'}"

    body_content = f"""
        <h1 style="{STYLES['h1']}">{html.escape(heading)}</h1>
        <p style="{STYLES['p']}">Hi {safe_username}, here are the most active discussions from the past week: {posts} and {comments} in your communities.</p>
        {panel}
        <a href="{html.escape(base_url)}/communities" style="{_button_style(brand_color)}">Open communities</a>
    """
    return _send_notification_email(
        to=email,
        subject=heading,
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note="You get this email once a week. " + FOOTER_NOTE,
            logo_html=_brand_logo_html(logo_url, org_name),
            unsubscribe_url=f"{base_url}{SETTINGS_PATH}",
            unsubscribe_label=SETTINGS_LABEL,
            preheader=", ".join(_title(entry.title) for entry in shown)[:120],
            powered_by=powered_by,
            lang=lang,
        ),
        headers=_headers(one_click_url),
        sender_name=sender_name,
    )


KIND_NEW = "new"
KIND_DUE = "due"
KIND_REMINDER = "reminder"


@dataclass
class AssignmentEntry:
    kind: str
    title: str
    course_name: str
    # Ready to read, e.g. "Fri, Nov 20"; empty when there is no due date.
    due_text: str
    url: str


def _assignment_line(entry: AssignmentEntry) -> str:
    if entry.kind == KIND_REMINDER:
        return f"Due {entry.due_text}. You haven't submitted it yet."
    if entry.kind == KIND_DUE:
        return f"The due date is now {entry.due_text}."
    return f"New assignment. Due {entry.due_text}." if entry.due_text else "New assignment."


def assignment_heading(entries: list) -> str:
    if len(entries) != 1:
        return f"{len(entries)} assignment updates"
    entry = entries[0]
    title = _title(entry.title)
    if entry.kind == KIND_REMINDER:
        return f"{title} is due {entry.due_text}"
    if entry.kind == KIND_DUE:
        return f"New due date for {title}"
    return f"New assignment in {email_org_name(entry.course_name)}"


def send_assignment_email(
    email: str,
    username: str,
    org_name: str,
    entries: list,
    base_url: str,
    one_click_url: str = "",
    lang: str = "en",
    logo_url: Optional[str] = None,
    sender_name: Optional[str] = None,
    brand_color: Optional[str] = None,
    powered_by: bool = True,
):
    """New assignments, changed due dates and reminders for one learner."""
    safe_username = html.escape(email_user_name(username))
    safe_org_name = html.escape(email_org_name(org_name))
    heading = assignment_heading(entries)
    shown = entries[:UNREAD_LIST_LIMIT]
    rows = "".join(
        _unread_row(
            {"title": entry.title, "author": entry.course_name, "snippet": _assignment_line(entry), "count": 0},
            index == len(shown) - 1,
        )
        for index, entry in enumerate(shown)
    )
    more = len(entries) - len(shown)
    more_html = (
        f'<p style="{STYLES["footer_text"]} margin: -12px 0 24px 0;">'
        f"And {more} more assignment{'s' if more > 1 else ''}.</p>"
        if more > 0 else ""
    )
    if len(entries) == 1:
        cta_url, cta_label = entries[0].url, "Open assignment"
    else:
        cta_url, cta_label = f"{base_url}/calendar", "Open calendar"

    body_content = f"""
        <h1 style="{STYLES['h1']}">{html.escape(heading)}</h1>
        <p style="{STYLES['p']}">Hi {safe_username}, here's what changed in your courses at {safe_org_name}.</p>
        <table role="presentation" class="lh-panel" width="100%" cellpadding="0" cellspacing="0" border="0"
               style="border: 1px solid {COLORS['border']}; border-radius: 10px; border-collapse: separate; margin: 0 0 24px 0;">
            {rows}
        </table>{more_html}
        <a href="{html.escape(cta_url)}" style="{_button_style(brand_color)}">{cta_label}</a>
    """
    first = shown[0]
    return _send_notification_email(
        to=email,
        subject=heading if len(entries) == 1 else f"{heading} in {email_org_name(org_name)}",
        body=_email_layout(
            title=heading,
            body_content=body_content,
            footer_note=FOOTER_NOTE,
            logo_html=_brand_logo_html(logo_url, org_name),
            unsubscribe_url=f"{base_url}{SETTINGS_PATH}",
            unsubscribe_label=SETTINGS_LABEL,
            preheader=f"{_title(first.title)}: {_assignment_line(first)}"[:120],
            powered_by=powered_by,
            lang=lang,
        ),
        headers=_headers(one_click_url),
        sender_name=sender_name,
    )
