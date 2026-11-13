"""Instance-wide Authentik configuration, read from the environment.

Secrets are only ever read from the environment (or a ``*_FILE`` path, for
Docker/Unraid secrets) and are never stored in the database, returned by an
endpoint, or logged.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, FrozenSet, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ENV_PREFIX = "LEARNHOUSE_AUTHENTIK_"

# Roles a group may map to, by their stable role_uuid.
ROLE_ALIASES: Dict[str, str] = {
    "admin": "role_global_admin",
    "maintainer": "role_global_maintainer",
    "instructor": "role_global_instructor",
    "user": "role_global_user",
}
# Highest privilege first: when a user is in several mapped groups the most
# privileged mapping wins, deterministically.
ROLE_PRIORITY: Tuple[str, ...] = (
    "role_global_admin",
    "role_global_maintainer",
    "role_global_instructor",
    "role_global_user",
)

# Asymmetric algorithms only. HS* would make the shared client secret a token
# signing key and "none" is never acceptable.
SUPPORTED_ID_TOKEN_ALGS: FrozenSet[str] = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)


class AuthentikConfigError(ValueError):
    """The Authentik environment configuration is invalid."""


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


def _env_secret(name: str) -> Optional[str]:
    """Read ``NAME`` or the file named by ``NAME_FILE`` (Docker secrets)."""
    path = _env(name + "_FILE")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError as exc:
            raise AuthentikConfigError(f"{ENV_PREFIX}{name}_FILE could not be read") from exc
        return value or None
    return _env(name)


def _csv(raw: Optional[str]) -> Tuple[str, ...]:
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def parse_role_mapping(raw: Optional[str]) -> Dict[str, str]:
    """``"group-a=admin,group b=instructor"`` -> ``{"group-a": "role_global_admin", ...}``.

    Unknown roles are a configuration error rather than silently ignored: a
    typo in a privilege mapping must be loud.
    """
    mapping: Dict[str, str] = {}
    for entry in _csv(raw):
        group, sep, role = entry.rpartition("=")
        group, role = group.strip(), role.strip().lower()
        if not sep or not group or not role:
            raise AuthentikConfigError(
                f"{ENV_PREFIX}ROLE_MAPPING entry {entry!r} must look like 'group=role'"
            )
        role_uuid = ROLE_ALIASES.get(role) or (role if role in ROLE_PRIORITY else None)
        if role_uuid is None:
            raise AuthentikConfigError(
                f"{ENV_PREFIX}ROLE_MAPPING role {role!r} must be one of "
                f"{', '.join(ROLE_ALIASES)}"
            )
        mapping[group] = role_uuid
    return mapping


def _validate_url(name: str, value: str, allow_http: bool) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise AuthentikConfigError(f"{ENV_PREFIX}{name} must be an absolute http(s) URL")
    if parts.scheme == "http" and not allow_http:
        raise AuthentikConfigError(
            f"{ENV_PREFIX}{name} must use https (set {ENV_PREFIX}ALLOW_INSECURE_HTTP=true "
            "only for local testing)"
        )
    if parts.username or parts.password:
        raise AuthentikConfigError(f"{ENV_PREFIX}{name} must not embed credentials")
    if parts.fragment:
        raise AuthentikConfigError(f"{ENV_PREFIX}{name} must not contain a fragment")
    return value


def _default_base_url() -> Optional[str]:
    """``https://{LEARNHOUSE_DOMAIN}`` from the main LearnHouse config.

    Uses ``domain``, not ``frontend_domain``: the latter silently defaults to
    ``localhost:3000`` when LEARNHOUSE_FRONTEND_DOMAIN is unset.
    """
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
class AuthentikSettings:
    enabled: bool
    issuer: str = ""
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    redirect_uri: str = ""
    post_logout_redirect_uri: str = ""
    scopes: Tuple[str, ...] = ("openid", "email", "profile")
    display_name: str = "Authentik"
    allowed_groups: FrozenSet[str] = frozenset()
    role_mapping: Dict[str, str] = field(default_factory=dict)
    role_sync: bool = True
    auto_provision: bool = True
    link_existing_by_email: bool = True
    bypass_invite: bool = False
    ca_bundle: Optional[str] = None
    allow_insecure_http: bool = False
    http_timeout: float = 10.0
    clock_skew_seconds: int = 60

    @property
    def issuer_normalized(self) -> str:
        return self.issuer.rstrip("/")


def _load() -> AuthentikSettings:
    if not _env_bool("ENABLED", False):
        return AuthentikSettings(enabled=False)

    allow_http = _env_bool("ALLOW_INSECURE_HTTP", False)
    issuer = _env("ISSUER")
    client_id = _env("CLIENT_ID")
    client_secret = _env_secret("CLIENT_SECRET")
    missing = [
        n for n, v in (("ISSUER", issuer), ("CLIENT_ID", client_id), ("CLIENT_SECRET", client_secret)) if not v
    ]
    if missing:
        raise AuthentikConfigError(
            "Authentik is enabled but these are not set: "
            + ", ".join(ENV_PREFIX + m for m in missing)
        )
    assert issuer and client_id and client_secret  # narrowed above
    _validate_url("ISSUER", issuer, allow_http)

    base = _default_base_url()
    redirect_uri = _env("REDIRECT_URI") or (f"{base}/auth/callback/authentik" if base else None)
    if not redirect_uri:
        raise AuthentikConfigError(f"{ENV_PREFIX}REDIRECT_URI is required")
    _validate_url("REDIRECT_URI", redirect_uri, allow_http)
    if urlsplit(redirect_uri).query:
        raise AuthentikConfigError(f"{ENV_PREFIX}REDIRECT_URI must not contain a query string")

    # Default: the site root of the same origin as the redirect URI.
    redirect_parts = urlsplit(redirect_uri)
    post_logout = _env("POST_LOGOUT_REDIRECT_URI") or f"{redirect_parts.scheme}://{redirect_parts.netloc}/"
    _validate_url("POST_LOGOUT_REDIRECT_URI", post_logout, allow_http)

    scopes = _csv((_env("SCOPES") or "openid,email,profile").replace(" ", ","))
    if "openid" not in scopes:
        scopes = ("openid",) + scopes

    try:
        timeout = float(_env("HTTP_TIMEOUT") or 10)
    except ValueError as exc:
        raise AuthentikConfigError(f"{ENV_PREFIX}HTTP_TIMEOUT must be a number") from exc

    ca_bundle = _env("CA_BUNDLE")
    if ca_bundle and not os.path.isfile(ca_bundle):
        raise AuthentikConfigError(f"{ENV_PREFIX}CA_BUNDLE does not point at a file")

    return AuthentikSettings(
        enabled=True,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        post_logout_redirect_uri=post_logout,
        scopes=scopes,
        display_name=_env("DISPLAY_NAME") or "Authentik",
        allowed_groups=frozenset(_csv(_env("ALLOWED_GROUPS"))),
        role_mapping=parse_role_mapping(_env("ROLE_MAPPING")),
        role_sync=_env_bool("ROLE_SYNC", True),
        auto_provision=_env_bool("AUTO_PROVISION", True),
        link_existing_by_email=_env_bool("LINK_EXISTING_BY_EMAIL", True),
        bypass_invite=_env_bool("BYPASS_INVITE", False),
        ca_bundle=ca_bundle,
        allow_insecure_http=allow_http,
        http_timeout=max(1.0, min(timeout, 60.0)),
    )


@lru_cache(maxsize=1)
def get_authentik_settings() -> AuthentikSettings:
    """Load once per process. A broken config disables the feature (fail closed)
    and is logged once, instead of taking the whole API down."""
    try:
        return _load()
    except AuthentikConfigError as exc:
        logger.error("Authentik sign-in is disabled: %s", exc)
        return AuthentikSettings(enabled=False)


def reset_authentik_settings_cache() -> None:
    get_authentik_settings.cache_clear()
