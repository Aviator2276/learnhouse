"""Instance-wide switches for community emails, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

ENV_DISABLED = "LEARNHOUSE_NOTIFICATION_EMAILS_DISABLED"
ENV_DELAY = "LEARNHOUSE_COMMUNITY_EMAILS_DELAY_MINUTES"

DEFAULT_DELAY_MINUTES = 10
MAX_DELAY_MINUTES = 24 * 60


@dataclass(frozen=True)
class CommunityEmailConfig:
    enabled: bool
    # How long a post waits before it is emailed, so an author can fix or
    # delete it first and a moderator can remove it.
    delay_minutes: int


def get_config() -> CommunityEmailConfig:
    disabled = os.environ.get(ENV_DISABLED, "").strip().lower() in {"1", "true", "yes", "on"}
    raw = os.environ.get(ENV_DELAY, "").strip()
    try:
        delay = int(raw) if raw else DEFAULT_DELAY_MINUTES
    except ValueError:
        delay = DEFAULT_DELAY_MINUTES
    return CommunityEmailConfig(enabled=not disabled, delay_minutes=max(0, min(delay, MAX_DELAY_MINUTES)))
