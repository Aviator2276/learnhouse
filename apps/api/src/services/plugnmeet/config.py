"""Instance-wide plugNmeet configuration, read from the environment.

The API secret is only ever read from the environment (or a ``*_FILE`` path,
for Docker/Unraid secrets). It is never stored in the database, returned by
an endpoint, or logged.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ENV_PREFIX = "LEARNHOUSE_PLUGNMEET_"


class PlugNmeetConfigError(ValueError):
    """The plugNmeet environment configuration is invalid."""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(ENV_PREFIX + name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}{name} must be a whole number") from exc
    return max(low, min(value, high))


def _env_secret(name: str) -> Optional[str]:
    """Read ``NAME`` or the file named by ``NAME_FILE`` (Docker secrets)."""
    path = _env(name + "_FILE")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            raise PlugNmeetConfigError(f"{ENV_PREFIX}{name}_FILE could not be read") from exc
        return value or None
    return _env(name)


def is_private_host(host: str) -> bool:
    """A host that can only be on the local network: a loopback/private/link
    local IP literal, a single-label Docker service name, or a reserved local
    suffix. Plain http to these is the expected LAN setup (Unraid)."""
    host = host.strip("[]").lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "." not in host or host == "host.docker.internal" or host.endswith((".local", ".lan", ".internal", ".home.arpa"))
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _validate_url(name: str, value: str, *, allow_http: bool) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}{name} must be an absolute http(s) URL")
    if parts.scheme == "http" and not allow_http:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}{name} must use https")
    if parts.username or parts.password:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}{name} must not embed credentials")
    if parts.query or parts.fragment:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}{name} must not contain a query string or fragment")
    return value.rstrip("/")


def _default_public_url() -> Optional[str]:
    """``https://{LEARNHOUSE_DOMAIN}`` from the main LearnHouse config."""
    try:
        from config.config import get_learnhouse_config

        cfg = get_learnhouse_config()
        domain = cfg.hosting_config.domain
        ssl = bool(getattr(cfg.hosting_config, "ssl", True))
    except Exception:  # pragma: no cover - config import is exercised elsewhere
        return None
    if not domain:
        return None
    return f"{'https' if ssl else 'http'}://{domain}"


@dataclass(frozen=True)
class PlugNmeetSettings:
    enabled: bool
    server_url: str = ""
    client_url: str = ""
    api_key: str = ""
    api_secret: str = field(default="", repr=False)
    # Where users come back to (LearnHouse's public origin), for "Leave".
    public_url: str = ""
    quick_room: bool = True
    webhook_enabled: bool = False
    # Seconds plugNmeet keeps an empty room before tearing it down.
    empty_timeout: int = 1800
    # Moderators may open the room this long before the scheduled start ...
    start_early_minutes: int = 60
    # ... and until this long after the scheduled end.
    end_grace_minutes: int = 60
    ca_bundle: Optional[str] = None
    http_timeout: float = 15.0


def _load() -> PlugNmeetSettings:
    if not _env_bool("ENABLED", False):
        return PlugNmeetSettings(enabled=False)

    server_url = _env("SERVER_URL")
    client_url = _env("CLIENT_URL")
    api_key = _env("API_KEY")
    api_secret = _env_secret("API_SECRET")
    missing = [
        n
        for n, v in (("SERVER_URL", server_url), ("CLIENT_URL", client_url), ("API_KEY", api_key), ("API_SECRET", api_secret))
        if not v
    ]
    if missing:
        raise PlugNmeetConfigError(
            "plugNmeet is enabled but these are not set: " + ", ".join(ENV_PREFIX + m for m in missing)
        )
    assert server_url and client_url and api_key and api_secret  # narrowed above
    if len(api_secret) < 16:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}API_SECRET is too short to be a real secret")

    # Server-to-server calls may use plain http only on the local network (or
    # with an explicit opt-in); the browser-facing client URL is always https.
    allow_http = _env_bool("ALLOW_INSECURE_HTTP", False)
    server_host = urlsplit(server_url).hostname or ""
    server_url = _validate_url("SERVER_URL", server_url, allow_http=allow_http or is_private_host(server_host))
    client_url = _validate_url("CLIENT_URL", client_url, allow_http=allow_http)

    public_url = _env("PUBLIC_URL") or _default_public_url()
    if not public_url:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}PUBLIC_URL (or LEARNHOUSE_DOMAIN) is required")
    public_url = _validate_url("PUBLIC_URL", public_url, allow_http=allow_http)

    try:
        timeout = float(_env("HTTP_TIMEOUT") or 15)
    except ValueError as exc:
        raise PlugNmeetConfigError(f"{ENV_PREFIX}HTTP_TIMEOUT must be a number") from exc

    ca_bundle = _env("CA_BUNDLE")
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise PlugNmeetConfigError(f"{ENV_PREFIX}CA_BUNDLE does not point at a file")

    return PlugNmeetSettings(
        enabled=True,
        server_url=server_url,
        client_url=client_url,
        api_key=api_key,
        api_secret=api_secret,
        public_url=public_url,
        quick_room=_env_bool("QUICK_ROOM", True),
        webhook_enabled=_env_bool("WEBHOOK_ENABLED", False),
        empty_timeout=_env_int("EMPTY_TIMEOUT", 1800, 60, 86400),
        start_early_minutes=_env_int("START_EARLY_MINUTES", 60, 0, 1440),
        end_grace_minutes=_env_int("END_GRACE_MINUTES", 60, 0, 1440),
        ca_bundle=ca_bundle,
        http_timeout=max(1.0, min(timeout, 60.0)),
    )


@lru_cache(maxsize=1)
def get_plugnmeet_settings() -> PlugNmeetSettings:
    """Load once per process. A broken config disables the feature (fail
    closed) and is logged once, instead of taking the whole API down."""
    try:
        return _load()
    except PlugNmeetConfigError as exc:
        logger.error("plugNmeet live classes are disabled: %s", exc)
        return PlugNmeetSettings(enabled=False)


def reset_plugnmeet_settings_cache() -> None:
    get_plugnmeet_settings.cache_clear()
