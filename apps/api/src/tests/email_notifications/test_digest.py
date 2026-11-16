"""The weekly digest: when it is due, what it holds, and that a week is sent once."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import select

from src.db.email_notifications import EmailNotificationSettings
from src.services.email_notifications import digest

from .conftest import (
    BASE,
    NOW,
    add_comment,
    add_community,
    add_discussion,
    add_member,
    set_pref,
    set_settings,
)

# NOW is a Monday, 12:00. The tests run with the server clock in UTC or not:
# rows are stamped relative to NOW's naive value either way.
MONDAY_NOON = NOW.astimezone().astimezone(timezone.utc)


def settings(**values) -> EmailNotificationSettings:
    return EmailNotificationSettings(org_id=1, **values)


def test_due_on_the_chosen_weekday_after_the_chosen_hour():
    now = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)  # a Monday
    assert digest.is_due(settings(), now)
    assert not digest.is_due(settings(digest_hour=10), now)
    assert not digest.is_due(settings(digest_weekday=1), now)
    assert not digest.is_due(settings(enabled=False), now)
    assert not digest.is_due(settings(digest_enabled=False), now)


def test_due_uses_the_organization_time_zone():
    now = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)  # Sunday evening in Chicago
    assert not digest.is_due(settings(timezone="America/Chicago", digest_hour=0), now)
    assert digest.is_due(settings(timezone="America/Chicago", digest_weekday=6, digest_hour=20), now)
    # An unknown zone is read as UTC instead of stopping the digest.
    assert digest.is_due(settings(timezone="Mars/Olympus", digest_hour=3), now)
    assert digest.zone("").key == "UTC"


def test_not_due_again_the_same_day():
    now = datetime(2026, 11, 16, 9, 0, tzinfo=timezone.utc)
    assert not digest.is_due(settings(last_digest_at=now - timedelta(hours=1)), now)
    assert digest.is_due(settings(last_digest_at=now - timedelta(days=7)), now)
    # SQLite hands the timestamp back without a zone.
    assert not digest.is_due(settings(last_digest_at=(now - timedelta(hours=1)).replace(tzinfo=None)), now)


async def test_digest_lists_the_week_most_active_first(db, school, outbox):
    quiet = await add_discussion(db, school.general, school.sam.id, "Quiet one", minutes_ago=60 * 24 * 2)
    busy = await add_discussion(db, school.general, school.kim.id, "Busy one", "Lots to say", minutes_ago=60 * 24 * 3)
    for n in range(3):
        await add_comment(db, busy, school.lee.id, f"reply {n}", minutes_ago=60 * (n + 1))
    old = await add_discussion(db, school.general, school.sam.id, "Old but active", minutes_ago=60 * 24 * 20)
    await add_comment(db, old, school.kim.id, minutes_ago=60)
    await add_comment(db, old, school.kim.id, "too old", minutes_ago=60 * 24 * 9)
    await add_discussion(db, school.general, school.sam.id, "Last month", minutes_ago=60 * 24 * 30)
    await add_discussion(db, school.general, school.sam.id, "Pinned", minutes_ago=60, pinned=True)

    stats = await digest.run_digests(db, MONDAY_NOON)
    assert stats.emails == 5 and stats.failed == 0
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "This week in Test Org"
    body = mail["body"]
    assert "Last month" not in body and "too old" not in body
    assert body.index("Pinned") < body.index("Busy one") < body.index("Old but active") < body.index("Quiet one")
    assert "3 new posts and 4 comments" in body
    assert f'{BASE}/communities"' in body and f"{BASE}/email-notifications" in body
    assert "once a week" in body
    assert mail["headers"]["List-Unsubscribe"].startswith("<")
    assert quiet.id


async def test_singular_counts(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    await add_comment(db, thread, school.kim.id, minutes_ago=30)
    await digest.run_digests(db, MONDAY_NOON)
    assert "1 new post and 1 comment in" in outbox.to("lee@test.com")[0]["body"]


async def test_a_week_is_sent_once(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    await digest.run_digests(db, MONDAY_NOON)
    sent = len(outbox.sent)
    assert sent == 5
    assert (await digest.run_digests(db, MONDAY_NOON + timedelta(minutes=5))).emails == 0
    assert len(outbox.sent) == sent
    row = (await db.execute(select(EmailNotificationSettings))).scalars().one()
    assert row.last_digest_at is not None
    # Next Monday it goes out again.
    await add_discussion(db, school.general, school.sam.id, minutes_ago=-60 * 24 * 6)
    assert (await digest.run_digests(db, MONDAY_NOON + timedelta(days=7))).emails == 5


async def test_members_who_turned_it_off_or_cannot_read_get_none(db, school, outbox):
    other = await add_community(db, 1, 2, "Showcase")
    await add_discussion(db, school.general, school.teacher.id, "In general", minutes_ago=60)
    await add_discussion(db, other, school.teacher.id, "In showcase", minutes_ago=60, label="showcase")
    await set_pref(db, school.sam.id, level="off")
    await set_pref(db, school.kim.id, weekly_digest=False)
    await set_pref(db, school.lee.id, muted_community_ids=[1], muted_labels=["showcase"])
    await add_member(db, 1, 6, "max", verified=False)
    stats = await digest.run_digests(db, MONDAY_NOON)
    # lee muted everything there is this week: no empty digest.
    assert outbox.recipients == {"ada@test.com", "tess@test.com"}
    assert stats.emails == 2


async def test_no_digest_when_the_week_was_silent_or_switched_off(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60 * 24 * 30)
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    row = (await db.execute(select(EmailNotificationSettings))).scalars().one()
    row.last_digest_at = None
    row.digest_enabled = False
    db.add(row)
    await db.commit()
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0
    assert outbox.sent == []


async def test_no_digest_on_another_day(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    await set_settings(db, digest_weekday=3)
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0


async def test_nobody_to_send_to(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    await set_settings(db, default_level="off")
    assert (await digest.run_digests(db, MONDAY_NOON)).considered == 0


async def test_organization_without_communities_has_no_week(db, org, roles):
    assert await digest.week_threads(db, 1, NOW) == []
    assert await digest.load_authors(db, []) == {}


async def test_provider_refusal_is_counted(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    outbox.result = False
    assert (await digest.run_digests(db, MONDAY_NOON)).failed == 5


async def test_exception_while_sending_is_counted(db, school, outbox):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)
    outbox.raises = RuntimeError("smtp down")
    assert (await digest.run_digests(db, MONDAY_NOON)).failed == 5


async def test_a_broken_organization_does_not_block_the_others(db, school, outbox, monkeypatch):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)

    async def boom(*args, **kwargs):
        raise RuntimeError("bad org")

    monkeypatch.setattr(digest, "send_org_digest", boom)
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0


async def test_organization_deleted_before_sending(db, school, outbox, monkeypatch):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)

    async def no_look(db, org_id):
        return None

    monkeypatch.setattr(digest, "load_org_look", no_look)
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0


@pytest.mark.parametrize("existing", [False, True])
async def test_second_replica_cannot_claim_the_same_week(db, school, existing):
    if existing:
        await set_settings(db)
    mine = (await db.execute(select(EmailNotificationSettings))).scalars().first() or settings()
    # The other replica claims first.
    theirs = (await db.execute(select(EmailNotificationSettings))).scalars().first() or settings()
    assert await digest._claim(db, theirs, MONDAY_NOON)
    stale = EmailNotificationSettings(org_id=1, id=mine.id, last_digest_at=None)
    assert not await digest._claim(db, stale, MONDAY_NOON)
    # With a known previous value the claim compares against it.
    current = (await db.execute(select(EmailNotificationSettings))).scalars().one()
    await db.refresh(current)
    assert await digest._claim(db, current, MONDAY_NOON + timedelta(days=7))


async def test_run_skips_an_organization_another_replica_claimed(db, school, outbox, monkeypatch):
    await add_discussion(db, school.general, school.sam.id, minutes_ago=60)

    async def lost(db, settings, now):
        return False

    monkeypatch.setattr(digest, "_claim", lost)
    assert (await digest.run_digests(db, MONDAY_NOON)).emails == 0 and outbox.sent == []


async def test_comments_on_threads_of_a_deleted_community_are_left_out(db, school, outbox):
    doomed = await add_community(db, 1, 2, "Doomed")
    thread = await add_discussion(db, doomed, school.sam.id, "Orphaned", minutes_ago=60 * 24 * 20)
    await add_comment(db, thread, school.kim.id, minutes_ago=30)
    await db.delete(doomed)
    await db.commit()
    assert await digest.week_threads(db, 1, NOW) == []


async def test_member_who_can_read_only_some_communities(db, school, outbox):
    from src.db.usergroup_resources import UserGroupResource
    from src.db.usergroups import UserGroup

    private = await add_community(db, 1, 2, "Staff room")
    db.add(UserGroup(id=1, org_id=1, name="Staff", description="", usergroup_uuid="usergroup_s"))
    await db.commit()
    db.add(UserGroupResource(usergroup_id=1, resource_uuid=private.community_uuid, org_id=1))
    await db.commit()
    await add_discussion(db, private, school.admin.id, "Staff only", minutes_ago=60)
    await add_discussion(db, school.general, school.admin.id, "For all", minutes_ago=60)
    await digest.run_digests(db, MONDAY_NOON)
    assert "Staff only" not in outbox.to("sam@test.com")[0]["body"]
    assert "Staff only" in outbox.to("ada@test.com")[0]["body"]


async def test_org_look_for_a_missing_organization(db, school):
    from src.services.email_notifications.delivery import load_org_look

    assert await load_org_look(db, 404) is None
