"""The live-class endpoints against the fake plugNmeet server, with a real
SQLite database, fakeredis and LearnHouse's real RBAC."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.courses import Course
from src.db.live_classes import LiveClass, LiveClassAttendance
from src.db.users import AnonymousUser, APITokenUser
from src.routers import plugnmeet as router
from src.services.calendar.types import CalendarContext
from src.services.plugnmeet import calendar_source
from src.services.plugnmeet import classes as svc

from .conftest import PUBLIC, add_member, maintainer_role, make_request, make_settings, webhook_token

NOW = datetime.now(timezone.utc)


def req():
    return make_request()


async def _create(db, user, org_id=1, **overrides):
    body = dict(
        title="Algebra live",
        description="Bring questions",
        starts_at=NOW + timedelta(minutes=30),
        duration_minutes=60,
        timezone="America/Chicago",
    )
    body.update(overrides)
    return await router.api_create_classes(req(), org_id, svc.ClassCreate(**body), current_user=user, db=db)


async def _list(db, user, when="upcoming", course_uuid=None, org_id=1):
    return await router.api_list_classes(req(), org_id, when=when, course_uuid=course_uuid, limit=50, current_user=user, db=db)


async def _join(db, user, class_uuid):
    return await router.api_join_class(req(), class_uuid, current_user=user, db=db)


def _code(exc: pytest.ExceptionInfo) -> object:
    detail = exc.value.detail
    return detail.get("code") if isinstance(detail, dict) else exc.value.status_code


@pytest.fixture
async def people(db, org, admin_user, regular_user, other_org):
    outsider = await add_member(db, other_org, user_id=9, uuid="user_outsider", role_id=0)
    return SimpleNamespace(admin=admin_user, learner=regular_user, outsider=outsider)


# ---------------------------------------------------------------------------
# Availability and access
# ---------------------------------------------------------------------------


async def test_config(env):
    assert (await router.api_config()).model_dump() == {"enabled": True, "quick_room": True, "attendance": True}
    env.settings = make_settings(enabled=False)
    assert (await router.api_config()).enabled is False


async def test_disabled_is_404(env, db, people):
    env.settings = make_settings(enabled=False)
    with pytest.raises(HTTPException) as exc:
        await _list(db, people.admin)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("who", [AnonymousUser(), None])
async def test_signed_out_is_401(env, db, people, who):
    with pytest.raises(HTTPException) as exc:
        await _list(db, who)
    assert exc.value.status_code == 401


async def test_api_token_is_401(env, db, people):
    token_user = APITokenUser(org_id=1, rights={}, token_name="t", created_by_user_id=1)
    with pytest.raises(HTTPException) as exc:
        await _list(db, token_user)
    assert exc.value.status_code == 401


async def test_non_member_sees_nothing(env, db, people):
    created = await _create(db, people.admin)
    for call in (
        lambda: _list(db, people.outsider),
        lambda: router.api_get_class(req(), created[0].class_uuid, current_user=people.outsider, db=db),
        lambda: _join(db, people.outsider, created[0].class_uuid),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404


async def test_unknown_org_and_class(env, db, people):
    with pytest.raises(HTTPException) as exc:
        await _list(db, people.admin, org_id=999)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        await router.api_get_class(req(), "nope", current_user=people.admin, db=db)
    assert exc.value.status_code == 404


async def test_permissions(env, db, people, course):
    p = await router.api_permissions(req(), 1, course_uuid=None, current_user=people.admin, db=db)
    assert p.can_schedule and p.can_host_quick_room
    p = await router.api_permissions(req(), 1, course_uuid=None, current_user=people.learner, db=db)
    assert not p.can_schedule and not p.can_host_quick_room
    p = await router.api_permissions(req(), 1, course_uuid=course.course_uuid, current_user=people.admin, db=db)
    assert p.can_schedule
    p = await router.api_permissions(req(), 1, course_uuid=course.course_uuid, current_user=people.learner, db=db)
    assert not p.can_schedule
    with pytest.raises(HTTPException):
        await router.api_permissions(req(), 1, course_uuid="course_missing", current_user=people.admin, db=db)


async def test_private_course_hidden_from_non_readers(env, db, people, course, monkeypatch):
    created = await _create(db, people.admin, course_uuid=course.course_uuid)
    real = svc.OrgAccess.can_read_course

    async def no_read(self, c):
        return False if self.user_id == people.learner.id else await real(self, c)

    monkeypatch.setattr(svc.OrgAccess, "can_read_course", no_read)
    assert await _list(db, people.learner) == []
    for call in (
        lambda: _list(db, people.learner, course_uuid=course.course_uuid),
        lambda: router.api_permissions(req(), 1, course_uuid=course.course_uuid, current_user=people.learner, db=db),
        lambda: router.api_get_class(req(), created[0].class_uuid, current_user=people.learner, db=db),
        lambda: _create(db, people.learner, course_uuid=course.course_uuid),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------


async def test_learner_cannot_schedule(env, db, people, course):
    for kwargs in ({}, {"course_uuid": course.course_uuid}):
        with pytest.raises(HTTPException) as exc:
            await _create(db, people.learner, **kwargs)
        assert exc.value.status_code == 403


async def test_schedule_weekly_series(env, db, people):
    created = await _create(db, people.admin, frequency="weekly", occurrences=3)
    assert len(created) == 3
    assert len({c.series_uuid for c in created}) == 1 and created[0].series_uuid
    assert all(c.status == "scheduled" and c.can_moderate for c in created)
    assert [c.duration_minutes for c in created] == [60, 60, 60]
    listed = await _list(db, people.learner)
    assert [c.class_uuid for c in listed] == [c.class_uuid for c in created]
    assert not listed[0].can_moderate and not listed[0].can_join and not listed[0].can_start
    assert listed[0].can_start is False
    assert (await _list(db, people.admin))[0].can_start is True


async def test_maintainer_can_schedule(env, db, org, people):
    await maintainer_role(db, org)
    maintainer = await add_member(db, org, user_id=5, uuid="user_maint", role_id=2)
    assert len(await _create(db, maintainer)) == 1


async def test_course_class(env, db, people, course):
    created = await _create(db, people.admin, course_uuid=course.course_uuid)
    assert created[0].course.course_uuid == course.course_uuid
    assert (await _list(db, people.learner, course_uuid=course.course_uuid))[0].course.name == course.name
    with pytest.raises(HTTPException):
        await _create(db, people.admin, course_uuid="course_missing")


async def test_bad_timezone(env, db, people):
    with pytest.raises(HTTPException) as exc:
        await _create(db, people.admin, timezone="Mars/Olympus")
    assert exc.value.status_code == 422


def test_body_validation():
    with pytest.raises(ValueError):
        svc.ClassCreate(title="  ", starts_at=NOW, duration_minutes=30)
    with pytest.raises(ValueError):
        svc.ClassCreate(title="x", starts_at=datetime(2030, 1, 1), duration_minutes=30)
    with pytest.raises(ValueError):
        svc.ClassCreate(title="x", starts_at=NOW, duration_minutes=5)
    with pytest.raises(ValueError):
        svc.ClassUpdate(title="  ")
    with pytest.raises(ValueError):
        svc.ClassUpdate(starts_at=datetime(2030, 1, 1))
    assert svc.ClassUpdate(title=" x ").title == "x"
    assert svc.ClassUpdate().title is None


def test_occurrences_keep_wall_clock_time_across_dst():
    first = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)  # 10:00 in Chicago (CDT)
    starts = svc.occurrence_starts(first, "America/Chicago", svc.Frequency.weekly, 2)
    assert [s.hour for s in starts] == [15, 16]  # 10:00 CDT, then 10:00 CST
    assert svc.occurrence_starts(first, "UTC", svc.Frequency.none, 5) == [first]
    daily = svc.occurrence_starts(first, "UTC", svc.Frequency.daily, 3)
    assert [d.day for d in daily] == [27, 28, 29]
    monthly = svc.occurrence_starts(datetime(2026, 1, 31, 9, tzinfo=timezone.utc), "UTC", svc.Frequency.monthly, 14)
    assert [(m.year, m.month, m.day) for m in monthly[:3]] == [(2026, 1, 31), (2026, 2, 28), (2026, 3, 31)]
    assert (monthly[-1].year, monthly[-1].month) == (2027, 2)
    assert svc.occurrence_starts(first, "UTC", "other", 2) == [first, first]  # type: ignore[arg-type]


async def test_update_this_and_following(env, db, people):
    created = await _create(db, people.admin, frequency="weekly", occurrences=3)
    second = created[1].class_uuid
    moved = await router.api_update_class(
        req(), second, svc.ClassUpdate(title="Moved", starts_at=datetime.fromisoformat(created[1].starts_at) + timedelta(hours=1),
                                       duration_minutes=30, description="d"),
        scope=svc.Scope.following, current_user=people.admin, db=db,
    )
    assert [m.title for m in moved] == ["Moved", "Moved"]
    assert [m.duration_minutes for m in moved] == [30, 30]
    for before, after in zip(created[1:], moved):
        assert datetime.fromisoformat(after.starts_at) - datetime.fromisoformat(before.starts_at) == timedelta(hours=1)
    first = await router.api_get_class(req(), created[0].class_uuid, current_user=people.admin, db=db)
    assert first.title == "Algebra live"
    zoned = await router.api_update_class(req(), created[0].class_uuid, svc.ClassUpdate(timezone="UTC"),
                                          scope=svc.Scope.this, current_user=people.admin, db=db)
    assert zoned[0].timezone == "UTC"
    with pytest.raises(HTTPException) as exc:
        await router.api_update_class(req(), second, svc.ClassUpdate(title="x"), scope=svc.Scope.this,
                                      current_user=people.learner, db=db)
    assert exc.value.status_code == 403


async def test_cancel_and_delete(env, db, people):
    created = await _create(db, people.admin, frequency="daily", occurrences=3)
    await _join(db, people.admin, created[0].class_uuid)  # opens the room
    cancelled = await router.api_cancel_class(req(), created[0].class_uuid, scope=svc.Scope.this, current_user=people.admin, db=db)
    assert cancelled[0].status == "cancelled"
    assert env.pnm.rooms == {}  # the live room was ended
    with pytest.raises(HTTPException) as exc:
        await _join(db, people.learner, created[0].class_uuid)
    assert _code(exc) == "CANCELLED"

    rows = await router.api_cancel_class(req(), created[1].class_uuid, scope=svc.Scope.following, current_user=people.admin, db=db)
    assert [r.status for r in rows] == ["cancelled", "cancelled"]

    db.add(LiveClassAttendance(class_id=(await svc.load_class(db, created[2].class_uuid))[0].id, user_id=people.learner.id,
                               joined_at=NOW))
    await db.commit()
    await router.api_delete_class(req(), created[1].class_uuid, scope=svc.Scope.following, current_user=people.admin, db=db)
    remaining = (await db.execute(select(LiveClass))).scalars().all()
    assert [r.class_uuid for r in remaining] == [created[0].class_uuid]
    assert (await db.execute(select(LiveClassAttendance))).scalars().all() == []

    with pytest.raises(HTTPException) as exc:
        await router.api_delete_class(req(), created[0].class_uuid, scope=svc.Scope.this, current_user=people.learner, db=db)
    assert exc.value.status_code == 403


async def test_cancel_survives_plugnmeet_outage(env, db, people):
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    env.pnm.overrides["/room/endRoom"] = lambda p: (502, b"down")
    rows = await router.api_cancel_class(req(), created[0].class_uuid, scope=svc.Scope.this, current_user=people.admin, db=db)
    assert rows[0].status == "cancelled"


async def test_delete_ends_live_room(env, db, people):
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    assert env.pnm.rooms
    await router.api_delete_class(req(), created[0].class_uuid.removeprefix("liveclass_"), scope=svc.Scope.this,
                                  current_user=people.admin, db=db)
    assert env.pnm.rooms == {}


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------


async def test_join_flow(env, db, people):
    created = await _create(db, people.admin)
    uuid = created[0].class_uuid

    with pytest.raises(HTTPException) as exc:
        await _join(db, people.learner, uuid)
    assert _code(exc) == "NOT_STARTED"
    assert env.pnm.rooms == {}  # a learner never opens a room

    host = await _join(db, people.admin, uuid)
    assert host.join_url == "https://meet.example.com/?access_token=tok-1"
    room_id = "lh-" + uuid.removeprefix("liveclass_")
    meta = env.pnm.rooms[room_id]["metadata"]
    assert meta["logout_url"] == f"{PUBLIC}/live-classes/{uuid.removeprefix('liveclass_')}"
    assert meta["webhook_url"] == f"{PUBLIC}/api/v1/plugnmeet/webhook"
    assert meta["room_title"] == "Algebra live"
    assert env.pnm.tokens[0]["user_info"]["is_admin"] is True
    assert env.pnm.tokens[0]["user_info"]["user_id"] == "lh_admin"
    assert env.pnm.tokens[0]["user_info"]["name"] == "Admin User"

    learner = await _join(db, people.learner, uuid)
    assert learner.join_url.endswith("tok-2")
    info = env.pnm.tokens[1]["user_info"]
    assert info["is_admin"] is False and info["user_id"] == "lh_regular"
    creates = [c for c in env.pnm.calls if c[0] == "/room/create"]
    assert len(creates) == 1

    detail = await router.api_get_class(req(), uuid, current_user=people.learner, db=db)
    assert detail.status == "live" and detail.can_join

    with pytest.raises(HTTPException) as exc:
        await router.api_end_class(req(), uuid, current_user=people.learner, db=db)
    assert exc.value.status_code == 403
    env.redis.flushall()
    await router.api_end_class(req(), uuid, current_user=people.admin, db=db)
    assert env.pnm.rooms == {}


async def test_webhook_url_omitted_without_attendance(env, db, people):
    env.settings = make_settings(webhook_enabled=False)
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    assert "webhook_url" not in next(iter(env.pnm.rooms.values()))["metadata"]


async def test_avatar_sent_absolute(env, db, org, people):
    member = await add_member(db, org, user_id=6, uuid="user_pic", role_id=4, username="pic", avatar="me 1.png")
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    await _join(db, member, created[0].class_uuid)
    assert env.pnm.tokens[-1]["user_info"]["user_metadata"]["profile_pic"] == f"{PUBLIC}/content/users/user_pic/avatars/me%201.png"
    assert env.pnm.tokens[-1]["user_info"]["name"] == "pic"


async def test_moderator_too_early_and_after_end(env, db, people):
    early = await _create(db, people.admin, starts_at=NOW + timedelta(days=2))
    with pytest.raises(HTTPException) as exc:
        await _join(db, people.admin, early[0].class_uuid)
    assert _code(exc) == "TOO_EARLY"
    past = await _create(db, people.admin, starts_at=NOW - timedelta(days=2))
    for who in (people.admin, people.learner):
        with pytest.raises(HTTPException) as exc:
            await _join(db, who, past[0].class_uuid)
        assert _code(exc) == "ENDED"
    assert (await _list(db, people.learner, when="past"))[0].status == "ended"


async def test_join_when_plugnmeet_is_down(env, db, people):
    created = await _create(db, people.admin)
    env.pnm.overrides["/room/isRoomActive"] = lambda p: (502, b"down")
    with pytest.raises(HTTPException) as exc:
        await _join(db, people.learner, created[0].class_uuid)
    assert exc.value.status_code == 503 and _code(exc) == "UNAVAILABLE"
    # A status check failure is reported as "scheduled", never as an error page.
    assert (await _list(db, people.learner))[0].status == "scheduled"
    env.pnm.overrides["/room/endRoom"] = lambda p: (502, b"down")
    with pytest.raises(HTTPException) as exc:
        await router.api_end_class(req(), created[0].class_uuid, current_user=people.admin, db=db)
    assert exc.value.status_code == 503


async def test_concurrent_start_creates_once(env, db, people):
    created = await _create(db, people.admin)
    room_id = "lh-" + created[0].class_uuid.removeprefix("liveclass_")
    # Another moderator holds the create lock and has created the room.
    env.redis.set(f"plugnmeet:create:{room_id}", "1")
    env.pnm.rooms[room_id] = {"metadata": {}}
    env.redis.set(f"plugnmeet:active:{room_id}", "0")
    await _join(db, people.admin, created[0].class_uuid)
    assert not [c for c in env.pnm.calls if c[0] == "/room/create"]


async def test_create_conflict_tolerated_if_room_is_live(env, db, people):
    created = await _create(db, people.admin)
    room_id = "lh-" + created[0].class_uuid.removeprefix("liveclass_")

    def already(payload):
        env.pnm.rooms[room_id] = payload
        return 200, {"status": False, "msg": "room already exists"}

    env.pnm.overrides["/room/create"] = already
    assert (await _join(db, people.admin, created[0].class_uuid)).join_url
    env.pnm.rooms.clear()
    env.redis.flushall()
    env.pnm.overrides["/room/create"] = lambda p: (200, {"status": False, "msg": "invalid"})
    with pytest.raises(HTTPException) as exc:
        await _join(db, people.admin, created[0].class_uuid)
    assert exc.value.status_code == 503


async def test_join_rate_limited(env, db, people, monkeypatch):
    created = await _create(db, people.admin)
    monkeypatch.setattr(router, "check_rate_limit", lambda key, n, w: (False, n, 30))
    with pytest.raises(HTTPException) as exc:
        await _join(db, people.learner, created[0].class_uuid)
    assert exc.value.status_code == 429


async def test_without_redis(env, db, people, monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    assert (await router.api_get_class(req(), created[0].class_uuid, current_user=people.learner, db=db)).status == "live"


async def test_live_now(env, db, people):
    soon = await _create(db, people.admin, title="Soon", starts_at=NOW + timedelta(minutes=5))
    await _create(db, people.admin, title="Later", starts_at=NOW + timedelta(minutes=45))
    running = await _create(db, people.admin, title="Running", starts_at=NOW - timedelta(minutes=10))
    await _join(db, people.admin, running[0].class_uuid)
    titles = [c.title for c in await _list(db, people.learner, when="now")]
    assert titles == ["Running", "Soon"]
    assert soon[0].title == "Soon"


# ---------------------------------------------------------------------------
# Quick meeting room
# ---------------------------------------------------------------------------


async def test_quick_room(env, db, people):
    status = await router.api_quick_status(req(), 1, current_user=people.learner, db=db)
    assert status.model_dump() == {"enabled": True, "active": False, "can_start": False}
    with pytest.raises(HTTPException) as exc:
        await router.api_quick_join(req(), 1, current_user=people.learner, db=db)
    assert _code(exc) == "NOT_STARTED"
    await router.api_quick_join(req(), 1, current_user=people.admin, db=db)
    assert env.pnm.rooms["lh-quick-1"]["metadata"]["logout_url"] == f"{PUBLIC}/live-classes"
    assert env.pnm.rooms["lh-quick-1"]["metadata"]["room_title"] == "Test Org meeting room"
    await router.api_quick_join(req(), 1, current_user=people.learner, db=db)
    assert env.pnm.tokens[-1]["user_info"]["is_admin"] is False
    assert (await router.api_quick_status(req(), 1, current_user=people.admin, db=db)).can_start
    with pytest.raises(HTTPException) as exc:
        await router.api_quick_end(req(), 1, current_user=people.learner, db=db)
    assert exc.value.status_code == 403
    await router.api_quick_end(req(), 1, current_user=people.admin, db=db)
    assert "lh-quick-1" not in env.pnm.rooms
    with pytest.raises(HTTPException):
        await router.api_quick_join(req(), 1, current_user=people.outsider, db=db)


async def test_quick_room_disabled_or_down(env, db, people):
    env.pnm.overrides["/room/isRoomActive"] = lambda p: (502, b"down")
    with pytest.raises(HTTPException) as exc:
        await router.api_quick_status(req(), 1, current_user=people.admin, db=db)
    assert exc.value.status_code == 503
    env.settings = make_settings(quick_room=False)
    assert (await router.api_quick_status(req(), 1, current_user=people.admin, db=db)).enabled is False
    for call in (router.api_quick_join, router.api_quick_end):
        with pytest.raises(HTTPException) as exc:
            await call(req(), 1, current_user=people.admin, db=db)
        assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Webhook and attendance
# ---------------------------------------------------------------------------


async def _hook(db, event: dict, *, token=None, headers=None):
    body = json.dumps(event).encode()
    sent = {"hash-token": token if token is not None else webhook_token(body), **(headers or {})}
    return await router.api_webhook(make_request("/api/v1/plugnmeet/webhook", sent, body), db=db)


_EVENT_IDS = iter(range(1, 1_000_000))


def _event(name, room_id, identity=None):
    event = {"event": name, "room": {"room_id": room_id}, "id": f"e{next(_EVENT_IDS)}", "created_at": "1"}
    if identity:
        event["participant"] = {"identity": identity}
    return event


async def test_attendance(env, db, people):
    created = await _create(db, people.admin)
    uuid = created[0].class_uuid
    room_id = "lh-" + uuid.removeprefix("liveclass_")
    assert (await _hook(db, _event("participant_joined", room_id, "lh_regular")))["result"] == "joined"
    assert (await _hook(db, _event("participant_joined", room_id, "lh_regular")))["result"] == "joined"  # reconnect
    assert (await _hook(db, _event("participant_joined", room_id, "lh_admin")))["result"] == "joined"
    assert (await _hook(db, _event("participant_left", room_id, "lh_regular")))["result"] == "left"
    report = await router.api_attendance(req(), uuid, current_user=people.admin, db=db)
    assert report.enabled
    by_user = {a.user_uuid: a for a in report.attendees}
    assert by_user["user_regular"].in_room is False and by_user["user_regular"].last_left_at
    assert by_user["user_admin"].in_room is True
    assert (await _hook(db, _event("room_finished", room_id)))["result"] == "closed"
    report = await router.api_attendance(req(), uuid, current_user=people.admin, db=db)
    assert not any(a.in_room for a in report.attendees)
    open_rows = (await db.execute(select(LiveClassAttendance).where(LiveClassAttendance.left_at == None))).all()  # noqa: E711
    assert open_rows == []
    with pytest.raises(HTTPException) as exc:
        await router.api_attendance(req(), uuid, current_user=people.learner, db=db)
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "event,result",
    [
        (_event("participant_joined", "lh-quick-1", "lh_regular"), "ignored"),
        (_event("participant_joined", "not-ours", "lh_regular"), "ignored"),
        (_event("participant_joined", "lh-00000000-0000-0000-0000-000000000000", "lh_regular"), "ignored"),
        ({"event": "room_started", "room": "bad"}, "ignored"),
    ],
)
async def test_webhook_ignores_foreign_rooms(env, db, people, event, result):
    assert (await _hook(db, event))["result"] == result


async def test_webhook_ignores_strangers_and_other_events(env, db, people):
    created = await _create(db, people.admin)
    room_id = "lh-" + created[0].class_uuid.removeprefix("liveclass_")
    for event in (
        _event("participant_joined", room_id, "lh_outsider"),
        _event("participant_joined", room_id, "lh_nobody"),
        _event("participant_joined", room_id),
        # Raw LearnHouse UUIDs and bare prefixes are not identities we issue.
        _event("participant_joined", room_id, "user_regular"),
        _event("participant_joined", room_id, "lh_"),
        {"event": "participant_joined", "room": {"room_id": room_id}, "participant": "x"},
        _event("track_published", room_id, "lh_regular"),
    ):
        assert (await _hook(db, event))["result"] == "ignored"


@pytest.mark.parametrize(
    "make_token",
    [
        lambda body: "",
        lambda body: "not-a-jwt",
        lambda body: webhook_token(body, secret="wrong-secret-0123456789abcdef"),
        lambda body: webhook_token(body, iss="someone-else"),
        lambda body: webhook_token(body, exp_in=-600),
        lambda body: webhook_token(b"other body"),
        lambda body: webhook_token(body, sha=123),
    ],
)
async def test_webhook_rejects_bad_signatures(env, db, people, make_token):
    body = json.dumps(_event("room_finished", "lh-x")).encode()
    with pytest.raises(HTTPException) as exc:
        await router.api_webhook(make_request("/w", {"authorization": make_token(body)}, body), db=db)
    assert exc.value.status_code == 401


async def test_webhook_rejects_none_algorithm(env, db, people):
    import jwt

    body = b"{}"
    token = jwt.encode({"iss": "plugnmeet", "exp": 9999999999, "sha256": "x"}, None, algorithm="none")
    with pytest.raises(HTTPException):
        await router.api_webhook(make_request("/w", {"hash-token": token}, body), db=db)


async def test_webhook_replay_and_bearer(env, db, people):
    body = json.dumps(_event("room_finished", "lh-x")).encode()
    token = webhook_token(body)
    assert (await router.api_webhook(make_request("/w", {"authorization": f"Bearer {token}"}, body), db=db))["ok"]
    with pytest.raises(HTTPException) as exc:
        await router.api_webhook(make_request("/w", {"hash-token": token}, body), db=db)
    assert exc.value.status_code == 401


@pytest.mark.parametrize("body", [b"not json", b"[1]"])
async def test_webhook_rejects_non_objects(env, db, people, body):
    with pytest.raises(HTTPException):
        await router.api_webhook(make_request("/w", {"hash-token": webhook_token(body)}, body), db=db)


async def test_webhook_needs_replay_cache(env, db, people, monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    with pytest.raises(HTTPException):
        await _hook(db, _event("room_finished", "lh-x"))


async def test_webhook_size_limits(env, db, people):
    with pytest.raises(HTTPException) as exc:
        await router.api_webhook(make_request("/w", {"content-length": "999999"}, b"{}"), db=db)
    assert exc.value.status_code == 413
    with pytest.raises(HTTPException) as exc:
        await router.api_webhook(make_request("/w", {"content-length": "abc"}, b"{}"), db=db)
    assert exc.value.status_code == 413
    with pytest.raises(HTTPException) as exc:
        await router.api_webhook(make_request("/w", {}, b"x" * (70 * 1024)), db=db)
    assert exc.value.status_code == 413


async def test_webhook_disabled(env, db, people):
    env.settings = make_settings(webhook_enabled=False)
    with pytest.raises(HTTPException) as exc:
        await _hook(db, _event("room_finished", "lh-x"))
    assert exc.value.status_code == 404


async def test_room_finished_clears_cache_without_redis(monkeypatch):
    from src.services.plugnmeet import webhook

    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    webhook._forget_active("lh-x")  # no error


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def _ctx(db, org, user, start=None, end=None):
    return CalendarContext(
        request=req(), db_session=db, current_user=user, user_id=user.id, org=org,
        start=start or NOW - timedelta(days=3), end=end or NOW + timedelta(days=3),
    )


async def test_calendar(env, db, org, people, course, monkeypatch):
    await _create(db, people.admin, title="Upcoming")
    await _create(db, people.admin, title="Running", starts_at=NOW - timedelta(minutes=5))
    await _create(db, people.admin, title="Done", starts_at=NOW - timedelta(days=1))
    cancelled = await _create(db, people.admin, title="Cancelled")
    await router.api_cancel_class(req(), cancelled[0].class_uuid, scope=svc.Scope.this, current_user=people.admin, db=db)
    hidden = await _create(db, people.admin, title="Hidden", course_uuid=course.course_uuid)

    events = await calendar_source.live_classes(_ctx(db, org, people.learner))
    by_title = {e.title: e for e in events}
    assert set(by_title) == {"Upcoming", "Running", "Done", "Hidden"}
    assert by_title["Upcoming"].status == "scheduled" and by_title["Upcoming"].type == "live_session"
    assert by_title["Running"].status == "live"
    assert by_title["Done"].status == "ended"
    assert by_title["Hidden"].course.course_uuid == course.course_uuid
    assert by_title["Upcoming"].url.startswith("/live-classes/")

    async def no_read(self, c):
        return False

    monkeypatch.setattr(svc.OrgAccess, "can_read_course", no_read)
    events = await calendar_source.live_classes(_ctx(db, org, people.learner))
    assert "Hidden" not in {e.title for e in events}
    assert hidden

    assert await calendar_source.live_classes(_ctx(db, org, people.outsider)) == []
    env.settings = make_settings(enabled=False)
    assert await calendar_source.live_classes(_ctx(db, org, people.admin)) == []


def test_calendar_source_is_registered():
    from src.services.calendar.sources import registered_sources

    assert "fork_plugnmeet_live_classes" in registered_sources()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def test_helpers():
    naive = datetime(2030, 1, 1, 12)
    assert svc._utc(naive).tzinfo is timezone.utc
    assert svc.iso(naive) == "2030-01-01T12:00:00Z"
    assert svc.normalize_class_uuid(" abc ") == "liveclass_abc"
    assert svc.normalize_class_uuid("liveclass_abc") == "liveclass_abc"
    assert svc.quick_room_id(7) == "lh-quick-7"
    user = SimpleNamespace(first_name="", last_name="", username="")
    assert svc.display_name(user) == "Member"


async def test_course_class_moderated_by_course_editor(env, db, org, people, course, monkeypatch):
    created = await _create(db, people.admin, course_uuid=course.course_uuid)

    async def editor(self, c):
        return self.user_id == people.learner.id

    monkeypatch.setattr(svc.OrgAccess, "can_edit_course", editor)
    detail = await router.api_get_class(req(), created[0].class_uuid, current_user=people.learner, db=db)
    assert detail.can_moderate
    course_row = (await db.execute(select(Course))).scalars().first()
    assert course_row.id == course.id


def test_make_client_uses_settings():
    from src.services.plugnmeet.client import PlugNmeetClient

    assert isinstance(router.make_client(make_settings()), PlugNmeetClient)
    assert svc.ClassUpdate(title=None).title is None


async def test_second_moderator_joins_open_room(env, db, people):
    created = await _create(db, people.admin)
    await _join(db, people.admin, created[0].class_uuid)
    await _join(db, people.admin, created[0].class_uuid)
    assert len([c for c in env.pnm.calls if c[0] == "/room/create"]) == 1


async def test_list_limit(env, db, people):
    await _create(db, people.admin, frequency="daily", occurrences=4)
    rows = await router.api_list_classes(req(), 1, when="upcoming", course_uuid=None, limit=2, current_user=people.learner, db=db)
    assert len(rows) == 2


async def test_access_edge_cases(env, db, org, people, course):
    from src.services.plugnmeet.permissions import org_access

    stranger = await org_access(req(), db, people.outsider, org.id)
    assert not await stranger.can_view(None) and not await stranger.can_moderate(None)
    member = await org_access(req(), db, people.learner, org.id)
    foreign = Course(id=99, name="x", description="", public=True, published=True, org_id=2, course_uuid="course_foreign",
                     creation_date="", update_date="")
    assert not await member.can_read_course(foreign)
