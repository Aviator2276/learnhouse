"""Runs the notification email jobs every five minutes from inside the API, so
no external cron is needed. Same shape as upstream's messaging scheduler: a
per-window lock in Redis keeps several replicas from each scanning, and the
jobs stay correct without it because each claims its work in the database
before sending.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from src.services.email_notifications.config import get_config

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 5 * 60
STARTUP_DELAY_SECONDS = 75
_LOCK_TTL_SECONDS = 4 * 60

_task: Optional[asyncio.Task] = None


def _lock_key(now: datetime) -> str:
    return f"learnhouse:notification:emails:{now.strftime('%Y-%m-%dT%H')}:{now.minute // 5}"


async def _claim(now: datetime) -> bool:
    """True when this replica should run this window (or Redis is absent)."""
    try:
        from src.core.redis import get_redis_client

        client = get_redis_client()
        if client is None:
            return True
        return bool(await asyncio.to_thread(client.set, _lock_key(now), "1", nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception as exc:
        logger.warning("Notification email lock unavailable, running anyway: %s", exc)
        return True


async def _tick() -> None:
    try:
        now = datetime.now(timezone.utc)
        if not await _claim(now):
            return
        from src.core.events.database import _async_session_factory
        from src.services.email_notifications.assignments import run_assignments
        from src.services.email_notifications.digest import run_digests
        from src.services.email_notifications.instant import run_instant

        async with _async_session_factory() as db_session:
            instant = await run_instant(db_session)
        async with _async_session_factory() as db_session:
            digest = await run_digests(db_session, now)
        async with _async_session_factory() as db_session:
            assignments = await run_assignments(db_session, now)
        sent = instant.emails + digest.emails + assignments.emails
        failed = instant.failed + digest.failed + assignments.failed
        if sent or failed:
            logger.info(
                "Notification emails: community=%s digest=%s assignments=%s failed=%s",
                instant.emails, digest.emails, assignments.emails, failed,
            )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A failed run must not kill the loop.
        logger.exception("Notification email run failed, will retry: %s", exc)


async def _loop() -> None:
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    while True:
        await _tick()
        await asyncio.sleep(INTERVAL_SECONDS)


def start_scheduler() -> None:
    """Start the tick. Never raises: it runs during startup.

    Idle under tests and when ``LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED`` is set.
    """
    global _task
    try:
        if os.environ.get("TESTING") == "true" or not get_config().enabled:
            return
        loop = _loop()
        try:
            _task = asyncio.create_task(loop)
        except Exception:
            loop.close()
            raise
    except Exception as exc:
        logger.warning("Notification email scheduler not started: %s", exc)


async def stop_scheduler() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
