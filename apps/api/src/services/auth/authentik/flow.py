"""Server-side storage of in-flight authorization requests.

``state`` is an opaque random handle; everything that matters (PKCE verifier,
nonce, org context, where to land afterwards, and the browser binding) stays in
Redis under a hash of it, is single-use (``GETDEL``) and expires after
``FLOW_TTL_SECONDS``. Redis being unavailable fails closed with a 503.

The *binding* is a second secret handed only to the browser tab that started
the flow (kept in sessionStorage). The callback must present it, so an attacker
cannot start a flow and trick a victim into completing it with the attacker's
Authentik account (login CSRF / session fixation).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

FLOW_TTL_SECONDS = 600
_KEY_PREFIX = "authentik:flow:"
_STATE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_MAX_NEXT_LEN = 2048


@dataclass(frozen=True)
class FlowState:
    code_verifier: str
    nonce: str
    binding_hash: str
    org_id: Optional[int]
    invite_code: Optional[str]
    next_path: str
    created_at: float


@dataclass(frozen=True)
class StartedFlow:
    state: str
    binding: str
    nonce: str
    code_challenge: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def safe_next_path(value: Optional[str]) -> str:
    """Only same-site relative paths. Anything else lands on ``/``.

    Rejects scheme-relative (``//evil``), backslash tricks (``/\\evil``),
    control characters and absolute URLs, i.e. every open-redirect shape.
    """
    if not value or not isinstance(value, str) or len(value) > _MAX_NEXT_LEN:
        return "/"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return "/"
    return value


def _redis():
    from src.core.redis import get_redis_client

    client = get_redis_client()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is temporarily unavailable, please try again.",
        )
    return client


def start_flow(*, org_id: Optional[int], invite_code: Optional[str], next_path: Optional[str]) -> StartedFlow:
    state = secrets.token_urlsafe(32)
    binding = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    # RFC 7636 §4.1: 43-128 chars from the unreserved set. 64 bytes -> 86 chars.
    verifier = secrets.token_urlsafe(64)
    record = FlowState(
        code_verifier=verifier,
        nonce=nonce,
        binding_hash=_hash(binding),
        org_id=org_id,
        invite_code=invite_code,
        next_path=safe_next_path(next_path),
        created_at=time.time(),
    )
    try:
        stored = _redis().set(_KEY_PREFIX + _hash(state), json.dumps(asdict(record)), ex=FLOW_TTL_SECONDS, nx=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Authentik flow state could not be stored: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is temporarily unavailable, please try again.",
        ) from exc
    if not stored:  # pragma: no cover - 256-bit collision
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Please try again.")
    return StartedFlow(state=state, binding=binding, nonce=nonce, code_challenge=pkce_challenge(verifier))


def consume_flow(state: str, binding: str) -> Optional[FlowState]:
    """Atomically fetch-and-delete the flow. ``None`` for unknown, expired,
    replayed or wrongly-bound state. Consumed even on a binding mismatch, so a
    leaked state cannot be retried."""
    if not isinstance(state, str) or not _STATE_RE.match(state):
        return None
    if not isinstance(binding, str) or not binding or len(binding) > 256:
        return None
    try:
        raw = _redis().getdel(_KEY_PREFIX + _hash(state))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Authentik flow state could not be read: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is temporarily unavailable, please try again.",
        ) from exc
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        record = FlowState(**data)
    except (TypeError, ValueError):
        return None
    if not hmac.compare_digest(record.binding_hash, _hash(binding)):
        logger.warning("Authentik callback presented a state with the wrong browser binding")
        return None
    if time.time() - record.created_at > FLOW_TTL_SECONDS:
        return None
    return record


def remember_logout_jti(jti: str, ttl_seconds: int) -> bool:
    """Record a back-channel logout ``jti``; False when it was already seen."""
    try:
        return bool(_redis().set("authentik:logout_jti:" + _hash(jti), "1", ex=ttl_seconds, nx=True))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Authentik logout replay cache unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Unavailable") from exc
