"""The in-process tick: locking, error handling, start and stop."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import fakeredis
import pytest

from src.services.email_notifications import scheduler
from src.services.email_notifications.delivery import Stats


@pytest.fixture
def jobs(monkeypatch):
    calls = []

    def job(name, stats):
        async def run(db, now=None):
            calls.append(name)
            return stats
        return run

    @asynccontextmanager
    async def session():
        yield object()

    monkeypatch.setattr("src.core.events.database._async_session_factory", session)
    monkeypatch.setattr("src.services.email_notifications.instant.run_instant", job("instant", Stats(emails=1)))
    monkeypatch.setattr("src.services.email_notifications.digest.run_digests", job("digest", Stats()))
    monkeypatch.setattr("src.services.email_notifications.assignments.run_assignments", job("assignments", Stats()))
    return calls


async def test_tick_runs_all_three_jobs(jobs, monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    await scheduler._tick()
    assert jobs == ["instant", "digest", "assignments"]


async def test_quiet_tick_logs_nothing(jobs, monkeypatch, caplog):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)

    async def quiet(db, now=None):
        return Stats()

    monkeypatch.setattr("src.services.email_notifications.instant.run_instant", quiet)
    with caplog.at_level("INFO"):
        await scheduler._tick()
    assert "Notification emails" not in caplog.text


async def test_only_one_replica_runs_a_window(jobs, monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: client)
    now = datetime(2026, 11, 16, 12, 7, tzinfo=timezone.utc)
    assert scheduler._lock_key(now) == "learnhouse:notification:emails:2026-11-16T12:1"
    assert await scheduler._claim(now)
    assert not await scheduler._claim(now)
    jobs.clear()
    await scheduler._tick()
    await scheduler._tick()
    assert jobs == ["instant", "digest", "assignments"]


async def test_redis_trouble_does_not_stop_email(jobs, monkeypatch):
    def broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr("src.core.redis.get_redis_client", broken)
    assert await scheduler._claim(datetime.now(timezone.utc))


async def test_a_failing_job_does_not_kill_the_loop(jobs, monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)

    async def boom(db, now=None):
        raise RuntimeError("db down")

    monkeypatch.setattr("src.services.email_notifications.instant.run_instant", boom)
    await scheduler._tick()

    async def cancelled(db, now=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr("src.services.email_notifications.instant.run_instant", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await scheduler._tick()


async def test_start_and_stop(monkeypatch):
    ticks = []

    async def tick():
        ticks.append(1)

    monkeypatch.setattr(scheduler, "_tick", tick)
    monkeypatch.setattr(scheduler, "STARTUP_DELAY_SECONDS", 0)
    monkeypatch.setattr(scheduler, "INTERVAL_SECONDS", 3600)
    monkeypatch.delenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED", raising=False)

    # Idle under tests.
    scheduler.start_scheduler()
    assert scheduler._task is None
    await scheduler.stop_scheduler()

    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED", "true")
    scheduler.start_scheduler()
    assert scheduler._task is None

    monkeypatch.delenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED")
    scheduler.start_scheduler()
    assert scheduler._task is not None
    await asyncio.sleep(0.01)
    assert ticks == [1]
    await scheduler.stop_scheduler()
    assert scheduler._task is None


def test_start_never_raises(monkeypatch):
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.delenv("LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED", raising=False)
    # No running loop here: create_task fails, and that must be swallowed.
    scheduler.start_scheduler()
    assert scheduler._task is None
