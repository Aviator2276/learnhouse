"""Assignment emails: new assignments, due date changes and reminders."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlmodel import select

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.courses import Course
from src.db.email_notifications import (
    EmailNotificationAssignmentState,
    EmailNotificationCursor,
    EmailNotificationReminder,
)
from src.db.trail_runs import TrailRun
from src.db.trails import Trail
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.services.email_notifications import assignments, emails

from .conftest import BASE, NOW, add_discussion, add_member, set_pref, set_settings

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


async def add_course(db, course_id: int = 1, name: str = "Algebra", public: bool = True, org_id: int = 1) -> Course:
    course = Course(id=course_id, name=name, description="", public=public, published=True,
                    open_to_contributors=False, org_id=org_id, course_uuid=f"course_{course_id}",
                    creation_date=str(NOW), update_date=str(NOW))
    db.add(course)
    await db.commit()
    return course


async def enroll(db, course: Course, *user_ids: int) -> None:
    for user_id in user_ids:
        trail = (await db.execute(select(Trail).where(Trail.user_id == user_id))).scalars().first()
        if trail is None:
            trail = Trail(org_id=course.org_id, user_id=user_id, trail_uuid=f"trail_{user_id}")
            db.add(trail)
            await db.commit()
            await db.refresh(trail)
        db.add(TrailRun(trail_id=trail.id, course_id=course.id, org_id=course.org_id, user_id=user_id,
                        creation_date=str(NOW), update_date=str(NOW)))
    await db.commit()


async def add_assignment(db, course: Course, assignment_id: int, title: str = "Essay", *, due: str | None = None,
                         published: bool = True, activity_published: bool = True) -> Assignment:
    db.add(Activity(id=assignment_id, name=title, activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,
                    activity_sub_type=ActivitySubTypeEnum.SUBTYPE_ASSIGNMENT_ANY, content={},
                    published=activity_published, org_id=course.org_id, course_id=course.id,
                    activity_uuid=f"activity_{assignment_id}", creation_date=str(NOW), update_date=str(NOW)))
    assignment = Assignment(id=assignment_id, title=title, description="", due_date=due, published=published,
                            grading_type=GradingTypeEnum.NUMERIC, org_id=course.org_id, course_id=course.id,
                            chapter_id=1, activity_id=assignment_id, assignment_uuid=f"assignment_{assignment_id}",
                            creation_date=str(NOW), update_date=str(NOW))
    db.add(assignment)
    await db.commit()
    return assignment


async def change(db, assignment: Assignment, **values) -> None:
    for key, value in values.items():
        setattr(assignment, key, value)
    db.add(assignment)
    await db.commit()


async def seed(db):
    assert (await assignments.run_assignments(db, T0)).emails == 0


@pytest.fixture
async def classroom(db, school):
    course = await add_course(db)
    await enroll(db, course, school.sam.id, school.kim.id)
    return SimpleNamespace(course=course, **vars(school))


async def test_first_run_announces_nothing_and_places_both_cursors(db, classroom, outbox):
    await add_discussion(db, classroom.general, classroom.teacher.id)
    await add_assignment(db, classroom.course, 1, due="2026-11-17")
    await seed(db)
    assert outbox.sent == []
    cursor = await db.get(EmailNotificationCursor, 1)
    assert cursor.assignments_seeded and cursor.last_discussion_id == 1
    assert (await assignments.run_assignments(db, T0)).emails == 2  # only the reminders, later


async def test_first_run_after_the_post_job_keeps_its_cursor(db, classroom, outbox):
    db.add(EmailNotificationCursor(id=1, last_discussion_id=7, last_comment_id=9))
    await db.commit()
    await seed(db)
    cursor = await db.get(EmailNotificationCursor, 1)
    assert cursor.assignments_seeded and (cursor.last_discussion_id, cursor.last_comment_id) == (7, 9)


async def test_new_assignment_reaches_enrolled_learners_only(db, classroom, outbox):
    await seed(db)
    await add_assignment(db, classroom.course, 1, "Essay on primes", due="2026-11-30")
    stats = await assignments.run_assignments(db, T0)
    assert stats.emails == 2
    assert outbox.recipients == {"sam@test.com", "kim@test.com"}
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "New assignment in Algebra"
    assert "Essay on primes" in mail["body"] and "New assignment. Due Mon, Nov 30." in mail["body"]
    assert f"{BASE}/course/1/activity/1" in mail["body"] and "Open assignment" in mail["body"]
    assert mail["headers"]["List-Unsubscribe"].startswith("<")
    # And only once.
    assert (await assignments.run_assignments(db, T0 + timedelta(minutes=5))).emails == 0


async def test_new_assignment_without_a_due_date(db, classroom, outbox):
    await seed(db)
    await add_assignment(db, classroom.course, 1)
    await assignments.run_assignments(db, T0)
    assert "New assignment.</div>" in outbox.to("sam@test.com")[0]["body"]


async def test_draft_is_announced_when_it_is_published(db, classroom, outbox):
    await seed(db)
    draft = await add_assignment(db, classroom.course, 1, published=False)
    hidden = await add_assignment(db, classroom.course, 2, "In a draft activity", activity_published=False)
    assert (await assignments.run_assignments(db, T0)).emails == 0
    await change(db, draft, published=True)
    assert (await assignments.run_assignments(db, T0)).emails == 2
    assert hidden.id and "In a draft activity" not in outbox.sent[0]["body"]


async def test_due_date_set_or_moved_is_announced(db, classroom, outbox):
    essay = await add_assignment(db, classroom.course, 1, "Essay")
    await seed(db)
    await change(db, essay, due_date="2026-11-30T17:00:00")
    await assignments.run_assignments(db, T0)
    mail = outbox.to("kim@test.com")[0]
    assert mail["subject"] == "New due date for Essay"
    assert "The due date is now Mon, Nov 30 at 17:00 (UTC)." in mail["body"]
    outbox.sent.clear()
    await change(db, essay, due_date="2026-12-02")
    assert (await assignments.run_assignments(db, T0)).emails == 2
    assert "Wed, Dec 2" in outbox.sent[0]["body"]


async def test_due_time_is_shown_in_the_organization_time_zone(db, classroom, outbox):
    await set_settings(db, timezone="America/Chicago")
    essay = await add_assignment(db, classroom.course, 1)
    await seed(db)
    await change(db, essay, due_date="2026-11-30T17:00:00Z")
    await assignments.run_assignments(db, T0)
    assert "Mon, Nov 30 at 11:00 (CST)" in outbox.sent[0]["body"]


async def test_removed_past_or_unreadable_due_dates_are_not_announced(db, classroom, outbox):
    one = await add_assignment(db, classroom.course, 1, due="2026-11-30")
    two = await add_assignment(db, classroom.course, 2, due="2026-11-30")
    three = await add_assignment(db, classroom.course, 3, due="2026-11-30")
    await seed(db)
    await change(db, one, due_date=None)
    await change(db, two, due_date="2026-11-01")
    await change(db, three, due_date="next friday")
    assert (await assignments.run_assignments(db, T0)).emails == 0
    # Publishing an assignment that is already overdue is not news either.
    await add_assignment(db, classroom.course, 4, due="2026-11-01T08:00")
    assert (await assignments.run_assignments(db, T0)).emails == 0
    assert outbox.sent == []


async def test_unpublishing_is_recorded_silently(db, classroom, outbox):
    essay = await add_assignment(db, classroom.course, 1)
    await seed(db)
    await change(db, essay, published=False)
    assert (await assignments.run_assignments(db, T0)).emails == 0
    state = (await db.execute(select(EmailNotificationAssignmentState))).scalars().one()
    assert state.visible is False


async def test_reminder_goes_out_once_inside_the_lead_time(db, classroom, outbox):
    await add_assignment(db, classroom.course, 1, "Essay", due="2026-11-18")  # ends Nov 19 00:00
    await seed(db)
    assert (await assignments.run_assignments(db, T0)).emails == 0  # the day before starts at midnight
    later = datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc)
    assert (await assignments.run_assignments(db, later)).emails == 2
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "Essay is due Wed, Nov 18"
    assert "Due Wed, Nov 18. You haven&#x27;t submitted it yet." in mail["body"] or "You haven't submitted it yet." in mail["body"]
    assert (await assignments.run_assignments(db, later + timedelta(hours=3))).emails == 0
    assert len((await db.execute(select(EmailNotificationReminder))).scalars().all()) == 2


async def test_moving_the_due_date_allows_a_new_reminder(db, classroom, outbox):
    essay = await add_assignment(db, classroom.course, 1, due="2026-11-17")
    await seed(db)
    assert (await assignments.run_assignments(db, T0)).emails == 2
    outbox.sent.clear()
    await change(db, essay, due_date="2026-11-18")
    # The change is announced; the reminder for the new date is not sent on top of it.
    await assignments.run_assignments(db, datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc))
    assert [m["subject"] for m in outbox.to("sam@test.com")] == ["New due date for Essay"]
    assert (await assignments.run_assignments(db, datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc))).emails == 0


async def test_learners_who_submitted_are_not_reminded(db, classroom, outbox):
    await add_assignment(db, classroom.course, 1, due="2026-11-17")
    await seed(db)
    db.add(AssignmentUserSubmission(
        assignmentusersubmission_uuid="s1", submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
        grade=0, user_id=classroom.sam.id, assignment_id=1, creation_date=str(NOW), update_date=str(NOW)))
    db.add(AssignmentUserSubmission(
        assignmentusersubmission_uuid="s2", submission_status=AssignmentUserSubmissionStatus.PENDING,
        grade=0, user_id=classroom.kim.id, assignment_id=1, creation_date=str(NOW), update_date=str(NOW)))
    await db.commit()
    await assignments.run_assignments(db, T0)
    assert outbox.recipients == {"kim@test.com"}


async def test_member_choices(db, classroom, outbox):
    await enroll(db, classroom.course, classroom.lee.id)
    await set_pref(db, classroom.sam.id, assignment_updates=False, assignment_reminder_days=3)
    await set_pref(db, classroom.kim.id, assignment_reminders=False)
    await set_pref(db, classroom.lee.id, assignment_reminder_days=30)  # not a choice: falls back to the org's
    await seed(db)
    await add_assignment(db, classroom.course, 1, "Essay", due="2026-11-18")
    await assignments.run_assignments(db, T0)
    # sam: no announcement, but the 3-day reminder. kim and lee: the announcement.
    assert outbox.to("sam@test.com")[0]["subject"] == "Essay is due Wed, Nov 18"
    assert outbox.to("kim@test.com")[0]["subject"] == "New assignment in Algebra"
    assert outbox.to("lee@test.com")[0]["subject"] == "New assignment in Algebra"
    outbox.sent.clear()
    await assignments.run_assignments(db, datetime(2026, 11, 18, 1, 0, tzinfo=timezone.utc))
    assert outbox.recipients == {"lee@test.com"}


async def test_organization_reminder_time_and_switches(db, classroom, outbox):
    row = await set_settings(db, assignment_reminder_days=7)
    await add_assignment(db, classroom.course, 1, due="2026-11-21")
    await seed(db)
    assert (await assignments.run_assignments(db, T0)).emails == 2
    row.assignments_enabled = False
    db.add(row)
    await db.commit()
    await add_assignment(db, classroom.course, 2, due="2026-11-17")
    assert (await assignments.run_assignments(db, T0)).emails == 0
    row.assignments_enabled, row.enabled = True, False
    db.add(row)
    await db.commit()
    await add_assignment(db, classroom.course, 3, due="2026-11-17")
    assert (await assignments.run_assignments(db, T0)).emails == 0


async def test_several_updates_make_one_email(db, classroom, outbox):
    await seed(db)
    for n in range(1, 9):
        await add_assignment(db, classroom.course, n, f"Task {n}", due="2026-12-01")
    await assignments.run_assignments(db, T0)
    assert len(outbox.to("sam@test.com")) == 1
    mail = outbox.to("sam@test.com")[0]
    assert mail["subject"] == "8 assignment updates in Test Org"
    assert "And 2 more assignments." in mail["body"] and f'{BASE}/calendar"' in mail["body"]
    outbox.sent.clear()
    for n in range(9, 16):
        await add_assignment(db, classroom.course, n, f"Task {n}")
    await assignments.run_assignments(db, T0)
    assert "And 1 more assignment." in outbox.to("sam@test.com")[0]["body"]


async def test_only_people_who_can_still_read_the_course(db, classroom, outbox, other_org):
    private = await add_course(db, 2, "Private", public=False)
    gone = await add_member(db, 1, 8, "gone")
    outsider = await add_member(db, other_org.id, 9, "zed")
    unverified = await add_member(db, 1, 10, "fresh", verified=False)
    await enroll(db, classroom.course, gone.id, outsider.id, unverified.id)
    await enroll(db, private, classroom.sam.id, classroom.kim.id)
    db.add(UserGroup(id=1, org_id=1, name="Cohort", description="", usergroup_uuid="usergroup_c"))
    await db.commit()
    db.add(UserGroupResource(usergroup_id=1, resource_uuid=private.course_uuid, org_id=1))
    db.add(UserGroupUser(usergroup_id=1, user_id=classroom.kim.id, org_id=1))
    await db.commit()
    membership = (await db.execute(select(UserOrganization).where(UserOrganization.user_id == gone.id))).scalars().one()
    await db.delete(membership)
    await db.commit()
    await seed(db)
    await add_assignment(db, classroom.course, 1, "Open task", due="2026-11-17")
    await add_assignment(db, private, 2, "Private task", due="2026-11-17")
    await assignments.run_assignments(db, T0)
    # sam is enrolled in the private course but is not in its user group.
    assert outbox.recipients == {"sam@test.com", "kim@test.com"}
    assert "Private task" not in outbox.to("sam@test.com")[0]["body"]
    assert "Private task" in outbox.to("kim@test.com")[0]["body"]


async def test_access_check_failure_means_no_email(db, classroom, outbox, monkeypatch):
    await seed(db)
    await add_assignment(db, classroom.course, 1)

    async def boom(*args, **kwargs):
        raise RuntimeError("rbac down")

    monkeypatch.setattr(assignments, "check_resource_access", boom)
    assert (await assignments.run_assignments(db, T0)).emails == 0


async def test_enrolled_user_row_missing(db, classroom):
    await add_assignment(db, classroom.course, 1)
    item = (await assignments._load_items(db))[0]
    readers = assignments.Readers(db)
    assert await readers.can_read(item, 999) is False
    assert await readers.can_read(item, 999) is False


async def test_course_without_learners(db, school, outbox):
    course = await add_course(db)
    await seed(db)
    await add_assignment(db, course, 1, due="2026-11-17")
    stats = await assignments.run_assignments(db, T0)
    assert stats.emails == 0 and stats.considered == 2


async def test_failures_are_counted(db, classroom, outbox):
    await seed(db)
    await add_assignment(db, classroom.course, 1)
    outbox.result = False
    assert (await assignments.run_assignments(db, T0)).failed == 2
    await add_assignment(db, classroom.course, 2)
    outbox.raises = RuntimeError("smtp down")
    assert (await assignments.run_assignments(db, T0)).failed == 2


async def test_organization_deleted_before_sending(db, classroom, outbox, monkeypatch):
    await seed(db)
    await add_assignment(db, classroom.course, 1)

    async def no_look(db, org_id):
        return None

    monkeypatch.setattr(assignments, "load_org_look", no_look)
    assert (await assignments.run_assignments(db, T0)).emails == 0


async def test_replica_races_are_lost_quietly(db, classroom, outbox):
    essay = await add_assignment(db, classroom.course, 1, due="2026-11-30")
    await seed(db)
    item = (await assignments._load_items(db))[0]
    # Another replica wrote the first state row for a new assignment.
    assert not await assignments._record(db, item, None)
    # Another replica already recorded this change.
    stale = EmailNotificationAssignmentState(id=1, assignment_id=1, visible=True, due_date="2026-01-01")
    assert not await assignments._record(db, item, stale)
    # And a reminder can be claimed once.
    assert await assignments._claim_reminder(db, 1, classroom.sam.id, "2026-11-30")
    assert not await assignments._claim_reminder(db, 1, classroom.sam.id, "2026-11-30")
    assert essay is not None


def test_due_helpers():
    assert assignments.due_moment("") is None
    assert assignments.due_moment("2026-11-18") == datetime(2026, 11, 19, tzinfo=timezone.utc)
    assert assignments.due_moment("2026-11-18T09:30:00") == datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
    assert assignments.reminder_anchor("2026-11-18") == datetime(2026, 11, 18, tzinfo=timezone.utc)
    assert assignments.reminder_anchor("2026-11-18T09:30:00") == datetime(2026, 11, 18, 9, 30, tzinfo=timezone.utc)
    assert assignments.reminder_anchor("soon") is None
    assert assignments.due_text("", "UTC") == ""
    assert assignments.due_text("2026-11-08", "UTC") == "Sun, Nov 8"


def test_reminder_wording_for_a_single_entry():
    entry = emails.AssignmentEntry(kind=emails.KIND_REMINDER, title="Essay", course_name="Algebra",
                                   due_text="Wed, Nov 18", url="https://x")
    assert emails.assignment_heading([entry]) == "Essay is due Wed, Nov 18"


async def test_reminder_claimed_by_another_replica_is_not_sent(db, classroom, outbox, monkeypatch):
    await add_assignment(db, classroom.course, 1, due="2026-11-17")
    await seed(db)

    async def lost(db, assignment_id, user_id, due_raw):
        return False

    monkeypatch.setattr(assignments, "_claim_reminder", lost)
    assert (await assignments.run_assignments(db, T0)).emails == 0 and outbox.sent == []
