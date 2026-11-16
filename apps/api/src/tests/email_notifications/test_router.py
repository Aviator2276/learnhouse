"""The settings endpoints, the test email and the unsubscribe link."""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlmodel import select

from src.db.email_notifications import EmailNotificationPreference
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroups import UserGroup
from src.db.users import AnonymousUser, APITokenUser
from src.routers import email_notifications as router
from src.services.email_notifications import emails

from .conftest import BASE, add_comment, add_community, add_discussion, add_member, set_settings


async def prefs(db, user, org_id=1):
    return await router.api_get_preferences(org_id, current_user=user, db=db)


async def put(db, user, org_id=1, **body):
    return await router.api_put_preferences(org_id, router.PreferencesIn(**body), current_user=user, db=db)


def settings_body(**values):
    body = dict(enabled=True, default_level="highlights", digest_enabled=True, digest_weekday=0,
                digest_hour=8, timezone="UTC")
    body.update(values)
    return router.SettingsIn(**body)


@pytest.fixture
def limiter(monkeypatch):
    monkeypatch.setattr(router, "check_rate_limit", lambda key, n, window: (True, 1, 0))


async def test_defaults_for_a_member_who_never_chose(db, school, outbox):
    out = await prefs(db, school.sam)
    assert out.available and out.assignments_available and out.digest_available
    assert out.level == "highlights" and out.uses_default and out.default_level == "highlights"
    assert out.weekly_digest and out.assignment_updates and out.assignment_reminders
    assert out.assignment_reminder_days == 1
    assert [c.name for c in out.communities] == ["General"] and not out.communities[0].muted
    assert [label.id for label in out.labels] == ["general", "question", "idea", "announcement", "showcase"]
    assert not out.can_manage
    assert (await prefs(db, school.admin)).can_manage


async def test_member_saves_their_choices(db, school, outbox):
    await add_community(db, 1, 2, "Showcase")
    out = await put(
        db, school.sam, level="mentions", weekly_digest=False,
        muted_community_uuids=["community_2", "community_2", "community_999"],
        muted_labels=["showcase", "idea", "made-up"],
        assignment_updates=False, assignment_reminders=True, assignment_reminder_days=3,
    )
    assert out.level == "mentions" and not out.uses_default and not out.weekly_digest
    assert [c.muted for c in out.communities] == [False, True]
    assert [label.id for label in out.labels if label.muted] == ["idea", "showcase"]
    assert not out.assignment_updates and out.assignment_reminder_days == 3
    row = (await db.execute(select(EmailNotificationPreference))).scalars().one()
    assert row.muted_community_ids == [2] and row.muted_labels == ["idea", "showcase"]
    # Saving again updates the same row.
    await put(db, school.sam, level="all")
    assert len((await db.execute(select(EmailNotificationPreference))).scalars().all()) == 1


async def test_cannot_mute_or_learn_about_a_community_they_cannot_read(db, school, outbox):
    private = await add_community(db, 1, 2, "Staff room")
    db.add(UserGroup(id=1, org_id=1, name="Staff", description="", usergroup_uuid="usergroup_s"))
    await db.commit()
    db.add(UserGroupResource(usergroup_id=1, resource_uuid=private.community_uuid, org_id=1))
    await db.commit()
    out = await put(db, school.sam, level="all", muted_community_uuids=[private.community_uuid])
    assert [c.name for c in out.communities] == ["General"]
    assert (await db.execute(select(EmailNotificationPreference))).scalars().one().muted_community_ids == []
    assert [c.name for c in (await prefs(db, school.admin)).communities] == ["General", "Staff room"]


async def test_invalid_choices_are_rejected():
    with pytest.raises(ValidationError):
        router.PreferencesIn(level="loud")
    with pytest.raises(ValidationError):
        router.PreferencesIn(level="all", assignment_reminder_days=5)
    with pytest.raises(ValidationError):
        router.PreferencesIn(level="all", muted_community_uuids=["x"] * 501)
    with pytest.raises(ValidationError):
        settings_body(timezone="Mars/Olympus")
    with pytest.raises(ValidationError):
        settings_body(timezone="../../etc/passwd")
    with pytest.raises(ValidationError):
        settings_body(digest_hour=24)
    with pytest.raises(ValidationError):
        settings_body(digest_weekday=7)
    with pytest.raises(ValidationError):
        settings_body(default_level="loud")


@pytest.mark.parametrize("who", ["anonymous", "token", "none"])
async def test_only_signed_in_people(db, school, who):
    user = {"anonymous": AnonymousUser(), "token": APITokenUser.model_construct(id=1, org_id=1), "none": None}[who]
    for call in (
        router.api_get_preferences(1, current_user=user, db=db),
        router.api_get_settings(1, current_user=user, db=db),
        router.api_send_test(1, router.TestIn(kind="digest"), current_user=user, db=db),
    ):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 401


async def test_other_organizations_are_invisible(db, school, other_org, outbox):
    outsider = await add_member(db, other_org.id, 9, "zed")
    for call in (prefs(db, outsider), put(db, outsider, level="all"), prefs(db, school.sam, org_id=2),
                 router.api_get_settings(1, current_user=outsider, db=db)):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 404


async def test_settings_are_for_admins_and_maintainers(db, school, outbox):
    for user in (school.sam, school.teacher):
        for call in (
            router.api_get_settings(1, current_user=user, db=db),
            router.api_put_settings(1, settings_body(), current_user=user, db=db),
            router.api_send_test(1, router.TestIn(kind="instant"), current_user=user, db=db),
        ):
            with pytest.raises(HTTPException) as exc:
                await call
            assert exc.value.status_code == 403

    out = await router.api_get_settings(1, current_user=school.admin, db=db)
    assert out.instance_enabled and out.enabled and out.default_level == "highlights"
    assert (out.digest_weekday, out.digest_hour, out.timezone) == (0, 8, "UTC")
    assert out.assignments_enabled and out.assignment_reminder_days == 1 and out.delay_minutes == 10
    assert out.last_digest_at is None

    out = await router.api_put_settings(
        1,
        settings_body(enabled=False, default_level="mentions", digest_enabled=False, digest_weekday=4,
                      digest_hour=17, timezone="America/Chicago", assignments_enabled=False,
                      assignment_reminder_days=3),
        current_user=school.admin, db=db,
    )
    assert not out.enabled and out.default_level == "mentions" and out.timezone == "America/Chicago"
    assert out.assignment_reminder_days == 3 and not out.assignments_enabled
    # Saved, and what members now see.
    again = await router.api_put_settings(1, settings_body(default_level="all"), current_user=school.admin, db=db)
    assert again.enabled and again.default_level == "all"
    member = await prefs(db, school.sam)
    assert member.level == "all" and member.uses_default


async def test_switches_show_as_unavailable(db, school, outbox, monkeypatch):
    await set_settings(db, assignments_enabled=False, digest_enabled=False)
    out = await prefs(db, school.sam)
    assert out.available and not out.assignments_available and not out.digest_available
    monkeypatch.setenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED", "1")
    out = await prefs(db, school.sam)
    assert not out.available and not out.assignments_available
    assert not (await router.api_get_settings(1, current_user=school.admin, db=db)).instance_enabled


async def test_test_email_goes_to_the_caller_only(db, school, outbox, limiter):
    for kind, subject in (
        ("instant", "Alex Morgan posted in General"),
        ("digest", "This week in Test Org"),
        ("assignment", "Sample assignment is due tomorrow"),
    ):
        out = await router.api_send_test(1, router.TestIn(kind=kind), current_user=school.admin, db=db)
        assert out.sent_to == "ada@test.com"
        assert outbox.sent[-1]["subject"] == subject
    assert outbox.recipients == {"ada@test.com"}
    assert "sample post" in outbox.sent[0]["body"]


async def test_test_email_uses_real_discussions_when_there_are_some(db, school, outbox, limiter):
    from datetime import datetime

    thread = await add_discussion(db, school.general, school.sam.id, "Real topic", created=str(datetime.now()))
    await add_comment(db, thread, school.kim.id, created=str(datetime.now()))
    await router.api_send_test(1, router.TestIn(kind="digest"), current_user=school.admin, db=db)
    assert "Real topic" in outbox.sent[-1]["body"] and "1 new post and 1 comment" in outbox.sent[-1]["body"]
    await router.api_send_test(1, router.TestIn(kind="instant"), current_user=school.admin, db=db)
    assert outbox.sent[-1]["subject"] == "sam posted in General"
    assert f"{BASE}/community/1/discussion/1" in outbox.sent[-1]["body"]


async def test_test_email_falls_back_to_the_sample_when_everything_is_muted(db, school, outbox, limiter):
    from datetime import datetime

    await add_discussion(db, school.general, school.sam.id, "Real topic", created=str(datetime.now()))
    await put(db, school.admin, level="off", muted_community_uuids=["community_1"])
    await router.api_send_test(1, router.TestIn(kind="digest"), current_user=school.admin, db=db)
    assert "Welcome to the community" in outbox.sent[-1]["body"]


async def test_test_email_failures(db, school, outbox, limiter, monkeypatch):
    outbox.result = False
    with pytest.raises(HTTPException) as exc:
        await router.api_send_test(1, router.TestIn(kind="digest"), current_user=school.admin, db=db)
    assert exc.value.status_code == 502

    blank = await add_member(db, 1, 8, "blank", 1, email="")
    with pytest.raises(HTTPException) as exc:
        await router.api_send_test(1, router.TestIn(kind="digest"), current_user=blank, db=db)
    assert exc.value.status_code == 400


async def test_test_email_is_rate_limited(db, school, outbox, monkeypatch):
    seen = {}

    def limit(key, attempts, window):
        seen["call"] = (key, attempts, window)
        return (False, 6, 120)

    monkeypatch.setattr(router, "check_rate_limit", limit)
    with pytest.raises(HTTPException) as exc:
        await router.api_send_test(1, router.TestIn(kind="digest"), current_user=school.admin, db=db)
    assert exc.value.status_code == 429 and exc.value.detail["retry_after"] == 120
    assert seen["call"] == ("notification_test_email:1", 5, 600)
    assert outbox.sent == []


async def test_unsubscribe_page_changes_nothing(db, school, outbox):
    token = emails.make_token("user_sam", 1)
    page = await router.api_unsubscribe_page(token=token, db=db)
    text = page.body.decode()
    assert "Turn off email notifications?" in text and "sam@test.com" in text and "Test Org" in text
    assert f'value="{token}"' in text
    assert (await db.execute(select(EmailNotificationPreference))).scalars().first() is None


async def test_unsubscribe_turns_everything_off_and_is_repeatable(db, school, outbox):
    await put(db, school.sam, level="all", muted_labels=["idea"])
    token = emails.make_token("user_sam", 1)
    for kwargs in (dict(token=token, token_query=None), dict(token=None, token_query=token)):
        page = await router.api_unsubscribe(db=db, **kwargs)
        assert "Email notifications are off" in page.body.decode()
    out = await prefs(db, school.sam)
    assert out.level == "off" and not out.assignment_updates and not out.assignment_reminders
    # The other choices are kept for when they turn it back on.
    assert [label.id for label in out.labels if label.muted] == ["idea"]
    # A member who never saved anything can use the link too.
    await router.api_unsubscribe(token=emails.make_token("user_kim", 1), token_query=None, db=db)
    assert (await prefs(db, school.kim)).level == "off"
    assert (await prefs(db, school.lee)).level == "highlights"


async def test_unsubscribe_rejects_bad_tokens(db, school, other_org, outbox):
    good = emails.make_token("user_sam", 1)
    payload = good.split(".", 1)[1]
    bad_tokens = [
        "",
        "garbage",
        "1.",
        f"2.{payload}",                      # another organization's number on sam's signature
        f"x.{payload}",
        f"{'9' * 13}.{payload}",
        good[:-2] + ("aa" if not good.endswith("aa") else "bb"),
        emails.make_token("user_nobody", 1),  # signed, but no such user
        emails.make_token("user_sam", 77),    # signed, but no such organization
    ]
    for token in bad_tokens:
        page = await router.api_unsubscribe(token=token, token_query=None, db=db)
        assert "This link has expired" in page.body.decode(), token
        page = await router.api_unsubscribe_page(token=token, db=db)
        assert "This link has expired" in page.body.decode(), token
    assert (await router.api_unsubscribe(token=None, token_query=None, db=db)).status_code == 200
    assert (await db.execute(select(EmailNotificationPreference))).scalars().first() is None


def test_token_is_bound_to_one_organization():
    token = emails.make_token("user_sam", 1)
    assert emails.read_token(token) == ("user_sam", 1)
    assert emails.read_token(token.replace("1.", "2.", 1)) is None


def test_no_unsubscribe_headers_without_a_link():
    assert emails._headers("") is None
