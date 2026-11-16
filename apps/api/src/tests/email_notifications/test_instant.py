"""New posts and comments: who is emailed, and that nobody is emailed twice."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import select

from src.db.courses.courses import Course
from src.db.email_notifications import EmailNotificationCursor
from src.db.nudges import EmailPreference
from src.db.resource_authors import ResourceAuthor, ResourceAuthorshipEnum, ResourceAuthorshipStatusEnum
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.security.rbac import AccessAction, check_resource_access
from src.services.email_notifications import instant
from src.services.email_notifications.audience import load_org_audience

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


async def start(db):
    """The first run only places the cursor."""
    stats = await instant.run_instant(db, NOW)
    assert stats.emails == 0


async def run(db):
    return await instant.run_instant(db, NOW)


async def test_first_run_does_not_email_the_backlog(db, school, outbox):
    old = await add_discussion(db, school.general, school.teacher.id)
    await add_comment(db, old, school.sam.id)
    await start(db)
    assert outbox.sent == []
    cursor = await db.get(EmailNotificationCursor, 1)
    assert (cursor.last_discussion_id, cursor.last_comment_id) == (1, 1)
    assert (await run(db)).considered == 0


async def test_first_run_on_an_empty_site(db, school, outbox):
    await start(db)
    cursor = await db.get(EmailNotificationCursor, 1)
    assert (cursor.last_discussion_id, cursor.last_comment_id) == (0, 0)


async def test_instructor_post_reaches_everyone_on_the_default_level(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Exam week", "Read chapter 3", emoji="📣")
    stats = await run(db)
    assert stats.emails == 4 and stats.failed == 0
    # Everyone but the author.
    assert outbox.recipients == {"ada@test.com", "sam@test.com", "kim@test.com", "lee@test.com"}
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "tess posted in General"
    assert "Read chapter 3" in mail["body"] and "Exam week" in mail["body"]
    assert f"{BASE}/community/1/discussion/1" in mail["body"]
    assert f"{BASE}/email-notifications" in mail["body"]
    assert mail["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mail["headers"]["List-Unsubscribe"].startswith(f"<{BASE}/api/v1/email-notifications/unsubscribe?token=")


async def test_nothing_is_sent_twice(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)
    await run(db)
    first = len(outbox.sent)
    assert (await run(db)).emails == 0
    assert len(outbox.sent) == first


async def test_learner_post_only_reaches_people_who_chose_everything(db, school, outbox):
    await set_pref(db, school.kim.id, level="all")
    await start(db)
    await add_discussion(db, school.general, school.sam.id, "Study group?", label="announcement")
    await run(db)
    # A learner calling their post an announcement does not make it one.
    assert outbox.recipients == {"kim@test.com"}


async def test_mention_reaches_the_mentioned_member_only(db, school, outbox):
    await set_pref(db, school.lee.id, level="mentions")
    await start(db)
    await add_discussion(db, school.general, school.sam.id, "Notes", "thanks @Kim and @lee. Also @sam and @nobody")
    await run(db)
    assert outbox.recipients == {"kim@test.com", "lee@test.com"}
    assert outbox.to("kim@test.com")[0]["subject"] == "sam mentioned you in Notes"


async def test_mention_in_the_title_counts(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.sam.id, "@kim have a look", "x")
    await run(db)
    assert outbox.recipients == {"kim@test.com"}


async def test_reply_reaches_the_thread_starter_and_earlier_commenters(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id, "Question 4")
    await add_comment(db, thread, school.kim.id, "I think it is B")
    await start(db)
    await add_comment(db, thread, school.lee.id, "Agreed, B")
    await run(db)
    assert outbox.recipients == {"sam@test.com", "kim@test.com"}
    assert outbox.to("sam@test.com")[0]["subject"] == "lee replied in Question 4"
    assert "Agreed, B" in outbox.to("sam@test.com")[0]["body"]


async def test_a_later_commenter_is_not_treated_as_already_in_the_thread(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id)
    await start(db)
    await add_comment(db, thread, school.kim.id, "one")
    await add_comment(db, thread, school.lee.id, "two")
    await run(db)
    # kim hears about lee's comment; lee does not hear about kim's earlier one.
    assert outbox.recipients == {"sam@test.com", "kim@test.com"}
    assert "2</span>" in outbox.to("sam@test.com")[0]["body"]


async def test_replies_are_not_sent_on_the_mentions_level(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id)
    await set_pref(db, school.sam.id, level="mentions")
    await start(db)
    await add_comment(db, thread, school.kim.id)
    await run(db)
    assert outbox.sent == []


async def test_comment_from_an_instructor_is_news_not_a_highlight(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id)
    await set_pref(db, school.lee.id, level="all")
    await start(db)
    await add_comment(db, thread, school.teacher.id, "Good question")
    await run(db)
    assert outbox.recipients == {"sam@test.com", "lee@test.com"}
    assert outbox.to("lee@test.com")[0]["subject"] == "tess commented on Hello"


async def test_off_gets_nothing(db, school, outbox):
    await set_pref(db, school.sam.id, level="off")
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Hi", "hello @sam")
    await run(db)
    assert "sam@test.com" not in outbox.recipients


async def test_muted_community_and_muted_post_type(db, school, outbox):
    other = await add_community(db, 1, 2, "Showcase")
    await set_pref(db, school.sam.id, muted_community_ids=[school.general.id])
    await set_pref(db, school.kim.id, muted_labels=["showcase"])
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "In general", "hello @sam")
    await add_discussion(db, other, school.teacher.id, "Look at this", "and @kim too", label="showcase")
    await add_discussion(db, other, school.teacher.id, "Another", "no names", label="showcase")
    await run(db)
    # sam muted General: not even the mention. kim muted the post type: only the mention.
    sam = outbox.to("sam@test.com")[0]
    assert "In general" not in sam["body"] and "Look at this" in sam["body"]
    kim = outbox.to("kim@test.com")[0]
    assert "Look at this" in kim["body"] and "Another" not in kim["body"]


async def test_several_discussions_make_one_email(db, school, outbox):
    await start(db)
    for n in range(8):
        await add_discussion(db, school.general, school.teacher.id, f"Topic {n}")
    await run(db)
    assert len(outbox.to("sam@test.com")) == 1
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "8 discussions with new activity in Test Org"
    assert "And 2 more discussions." in mail["body"]
    assert f'{BASE}/communities"' in mail["body"]
    # Newest first.
    assert mail["body"].index("Topic 7") < mail["body"].index("Topic 2")


async def test_one_more_is_singular(db, school, outbox):
    await start(db)
    for n in range(7):
        await add_discussion(db, school.general, school.teacher.id, f"Topic {n}")
    await run(db)
    assert "And 1 more discussion." in outbox.to("sam@test.com")[0]["body"]


async def test_mention_then_plain_comment_keeps_the_stronger_reason(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id)
    await set_pref(db, school.kim.id, level="all")
    await start(db)
    await add_comment(db, thread, school.lee.id, "ping @kim")
    await add_comment(db, thread, school.lee.id, "second thought")
    await run(db)
    assert outbox.to("kim@test.com")[0]["subject"] == "lee mentioned you in Hello"


async def test_group_only_community_stays_inside_the_group(db, school, outbox):
    private = await add_community(db, 1, 2, "Cohort A")
    db.add(UserGroup(id=1, org_id=1, name="A", description="", usergroup_uuid="usergroup_a"))
    await db.commit()
    db.add(UserGroupResource(usergroup_id=1, resource_uuid=private.community_uuid, org_id=1))
    db.add(UserGroupUser(usergroup_id=1, user_id=school.sam.id, org_id=1))
    await db.commit()
    await start(db)
    await add_discussion(db, private, school.teacher.id, "Cohort only", "hi @kim")
    await run(db)
    # sam is in the group, ada manages communities. kim is mentioned but cannot read it.
    assert outbox.recipients == {"sam@test.com", "ada@test.com"}


async def test_public_community_still_only_emails_members(db, school, outbox, other_org):
    public = await add_community(db, 1, 2, "Open", public=True)
    await add_member(db, other_org.id, 9, "zed")
    await add_member(db, None, 10, "nomad")
    await start(db)
    await add_discussion(db, public, school.teacher.id, "Open day", "hi @zed @nomad")
    await run(db)
    assert outbox.recipients == {"ada@test.com", "sam@test.com", "kim@test.com", "lee@test.com"}


async def test_audience_never_exceeds_learnhouse_read_access(db, school, outbox):
    """Whoever this feature would email must pass LearnHouse's own read check."""
    private = await add_community(db, 1, 2, "Cohort A")
    public = await add_community(db, 1, 3, "Open", public=True)
    db.add(UserGroup(id=1, org_id=1, name="A", description="", usergroup_uuid="usergroup_a"))
    await db.commit()
    db.add(UserGroupResource(usergroup_id=1, resource_uuid=private.community_uuid, org_id=1))
    db.add(UserGroupUser(usergroup_id=1, user_id=school.sam.id, org_id=1))
    await db.commit()
    audience = await load_org_audience(db, 1)
    people = {p.id: p for p in (school.admin, school.teacher, school.sam, school.kim, school.lee)}
    for community in (school.general, private, public):
        for user_id in await audience.readers(db, community):
            decision = await check_resource_access(
                None, db, people[user_id], community.community_uuid, AccessAction.READ, raise_on_deny=False
            )
            assert decision.allowed, (community.name, people[user_id].username)


async def test_course_authors_count_as_instructors_in_their_course_community(db, school, outbox):
    db.add(Course(id=1, org_id=1, name="Algebra", description="", public=False, published=True, open_to_contributors=False,
                  course_uuid="course_algebra", creation_date=str(NOW), update_date=str(NOW)))
    await db.commit()
    db.add(ResourceAuthor(resource_uuid="course_algebra", user_id=school.kim.id,
                          authorship=ResourceAuthorshipEnum.CONTRIBUTOR,
                          authorship_status=ResourceAuthorshipStatusEnum.ACTIVE))
    db.add(ResourceAuthor(resource_uuid="course_algebra", user_id=school.lee.id,
                          authorship=ResourceAuthorshipEnum.CONTRIBUTOR,
                          authorship_status=ResourceAuthorshipStatusEnum.PENDING))
    await db.commit()
    course_community = await add_community(db, 1, 2, "Algebra talk", course_id=1)
    orphan = await add_community(db, 1, 3, "Old course", course_id=99)
    await start(db)
    await add_discussion(db, course_community, school.kim.id, "From the author")
    await add_discussion(db, course_community, school.kim.id, "Again from the author")
    await add_discussion(db, course_community, school.lee.id, "From a pending author")
    await add_discussion(db, school.general, school.kim.id, "Author elsewhere")
    await add_discussion(db, orphan, school.kim.id, "Course is gone")
    await run(db)
    body = outbox.to("sam@test.com")[0]["body"]
    assert "From the author" in body and "Again from the author" in body
    assert "From a pending author" not in body and "Author elsewhere" not in body and "Course is gone" not in body


async def test_organization_switch_stops_everything(db, school, outbox):
    await set_settings(db, enabled=False)
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Hi", "hello @sam")
    await run(db)
    assert outbox.sent == []


async def test_organization_default_level_applies_to_members_without_a_choice(db, school, outbox):
    await set_settings(db, default_level="mentions")
    await set_pref(db, school.kim.id, level="highlights")
    await set_pref(db, school.lee.id, level="nonsense")
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)
    await run(db)
    assert outbox.recipients == {"kim@test.com"}


async def test_bad_default_level_falls_back_to_highlights(db, school, outbox):
    await set_settings(db, default_level="loud", assignment_reminder_days=40)
    audience = await load_org_audience(db, 1)
    assert audience.preference(school.sam.id).level == "highlights"
    assert audience.preference(school.sam.id).reminder_days == 1


async def test_unverified_blocked_and_empty_addresses_are_skipped(db, school, outbox):
    await add_member(db, 1, 6, "newbie", verified=False)
    await add_member(db, 1, 7, "ghost", email="")
    db.add(EmailPreference(user_id=school.sam.id, lifecycle_opt_out=True))
    db.add(EmailPreference(user_id=school.kim.id, suppressed=True))
    await db.commit()
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)
    stats = await run(db)
    assert outbox.recipients == {"ada@test.com", "lee@test.com"}
    assert stats.emails == 2


async def test_posts_wait_for_the_delay_and_keep_their_order(db, school, outbox, monkeypatch):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Old enough", minutes_ago=30)
    await add_discussion(db, school.general, school.teacher.id, "Too new", minutes_ago=2)
    await add_discussion(db, school.general, school.teacher.id, "Behind the new one", minutes_ago=40)
    thread = await add_discussion(db, school.general, school.sam.id, "Thread", minutes_ago=50)
    await add_comment(db, thread, school.kim.id, "too new as well", minutes_ago=1)
    await run(db)
    body = outbox.to("lee@test.com")[0]["body"]
    assert "Old enough" in body and "Too new" not in body and "Behind the new one" not in body
    # Later, the rest goes out: nothing was skipped.
    outbox.sent.clear()
    await instant.run_instant(db, NOW + timedelta(minutes=15))
    body = outbox.to("lee@test.com")[0]["body"]
    assert "Too new" in body and "Behind the new one" in body
    assert "too new as well" in outbox.to("sam@test.com")[0]["body"]


async def test_delay_can_be_turned_off(db, school, outbox, monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_COMMUNITY_EMAILS_DELAY_MINUTES", "0")
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, minutes_ago=0)
    assert (await run(db)).emails == 4


async def test_posts_older_than_two_days_are_skipped_but_not_retried(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Stale", minutes_ago=60 * 24 * 3)
    await add_discussion(db, school.general, school.teacher.id, "No date", created="")
    thread = await add_discussion(db, school.general, school.sam.id, "T", minutes_ago=60 * 24 * 3)
    await add_comment(db, thread, school.kim.id, minutes_ago=60 * 24 * 3)
    stats = await run(db)
    assert stats.considered == 0 and outbox.sent == []
    cursor = await db.get(EmailNotificationCursor, 1)
    assert cursor.last_discussion_id == 3 and cursor.last_comment_id == 1


async def test_comment_on_an_old_thread_loads_the_thread(db, school, outbox):
    thread = await add_discussion(db, school.general, school.sam.id, "From last month", minutes_ago=60 * 24 * 30)
    await start(db)
    await add_comment(db, thread, school.kim.id, "still relevant")
    await run(db)
    assert outbox.to("sam@test.com")[0]["subject"] == "kim replied in From last month"


async def test_rows_whose_community_or_thread_is_gone_are_ignored(db, school, outbox):
    await start(db)
    gone = await add_community(db, 1, 5, "Gone")
    lost = await add_discussion(db, gone, school.teacher.id, "Lost")
    await db.delete(gone)
    await db.commit()
    thread = await add_discussion(db, school.general, school.sam.id, "Kept")
    orphan = await add_comment(db, thread, school.kim.id)
    orphan.discussion_id = 999
    db.add(orphan)
    await db.commit()
    assert lost.id
    stats = await run(db)
    assert stats.emails == 0 and outbox.sent == []


async def test_author_names_and_missing_authors(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.admin.id, "From Ada")
    stray = await add_discussion(db, school.general, school.teacher.id, "From nobody")
    stray.author_id = 999
    db.add(stray)
    await db.commit()
    await set_pref(db, school.sam.id, level="all")
    await run(db)
    body = outbox.to("sam@test.com")[0]["body"]
    assert "Ada Lovelace:" in body and "Someone:" in body


async def test_empty_post_body_falls_back_to_the_title(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id, "Only a title", "")
    await run(db)
    assert outbox.to("sam@test.com")[0]["body"].count("Only a title") >= 2


async def test_provider_refusal_and_crash_are_counted_not_raised(db, school, outbox):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)
    outbox.result = False
    stats = await run(db)
    assert stats.emails == 0 and stats.failed == 4
    await add_discussion(db, school.general, school.teacher.id)
    outbox.raises = RuntimeError("smtp down")
    stats = await instant.run_instant(db, NOW + timedelta(minutes=1))
    assert stats.failed == 4


async def test_second_replica_that_lost_the_race_sends_nothing(db, school, outbox, monkeypatch):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)
    real = instant._ready

    async def bump():
        cursor = await db.get(EmailNotificationCursor, 1)
        cursor.last_discussion_id = 50
        db.add(cursor)
        await db.commit()

    # Another replica moves the cursor after this one has read it.
    cursor = await db.get(EmailNotificationCursor, 1)
    stale = EmailNotificationCursor(id=1, last_discussion_id=cursor.last_discussion_id,
                                    last_comment_id=cursor.last_comment_id)
    await bump()

    async def stale_get(model, ident):
        return stale

    monkeypatch.setattr(db, "get", stale_get)
    assert real
    stats = await run(db)
    assert stats.emails == 0 and outbox.sent == []


async def test_organization_deleted_between_post_and_send(db, school, outbox, monkeypatch):
    await start(db)
    await add_discussion(db, school.general, school.teacher.id)

    async def no_look(db, org_id):
        return None

    monkeypatch.setattr(instant, "load_org_look", no_look)
    assert (await run(db)).emails == 0


async def test_members_of_an_empty_organization(db, org, roles, outbox):
    community = await add_community(db, 1, 1)
    outsider = await add_member(db, None, 3, "solo")
    await start(db)
    await add_discussion(db, community, outsider.id)
    assert (await run(db)).emails == 0


@pytest.mark.parametrize("rights", [None, "broken", {"courses": "yes"}])
async def test_roles_with_odd_rights_grant_nothing(db, org, outbox, rights):
    from .conftest import add_role

    await add_role(db, 3, "Odd", rights)
    await add_member(db, 1, 1, "odd", 3)
    await add_member(db, 1, 2, "sam", 3)
    community = await add_community(db, 1, 1)
    await start(db)
    await add_discussion(db, community, 1)
    assert (await run(db)).emails == 0
    assert (await db.execute(select(EmailNotificationCursor))).scalars().one().last_discussion_id == 1
