"""Minimal, signed plugNmeet server API client.

Every call is ``POST {server}/auth{method}`` with the JSON body signed as
``HASH-SIGNATURE: hex(HMAC-SHA256(api_secret, raw body))``. The body is
serialised once and those exact bytes are both signed and sent: letting the
HTTP library re-serialise it would break the signature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from src.services.plugnmeet.config import PlugNmeetSettings

logger = logging.getLogger(__name__)

# plugNmeet's room_id rule; user ids follow the same charset here so they are
# always safe in URLs, logs and the webhook mapping back to LearnHouse users.
_ID_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")
MAX_RESPONSE_BYTES = 256 * 1024


class PlugNmeetError(Exception):
    """plugNmeet refused or could not be reached. ``msg`` is plugNmeet's own
    message (never contains our secret) and is only logged, not shown."""

    def __init__(self, method: str, msg: str, *, unreachable: bool = False):
        super().__init__(f"plugNmeet {method}: {msg}")
        self.method = method
        self.msg = msg
        self.unreachable = unreachable


def sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def safe_id(value: str, *, max_len: int = 64) -> str:
    """``value`` with everything outside ``[A-Za-z0-9_-]`` removed. It is
    deterministic, so the same user always gets the same plugNmeet identity
    (a re-join replaces the stale session instead of duplicating it)."""
    cleaned = _ID_UNSAFE.sub("", value)[:max_len]
    if not cleaned:
        raise ValueError("identifier has no safe characters")
    return cleaned


def default_room_features() -> dict[str, Any]:
    """Teaching defaults: webcams, screen share, chat with uploads, whiteboard,
    shared notes, polls, six breakout rooms, cloud and local recording,
    no waiting room, everyone muted on entry."""
    return {
        "allow_webcams": True,
        "mute_on_start": True,
        "allow_screen_share": True,
        "allow_view_other_webcams": True,
        "allow_view_other_users_list": True,
        "admin_only_webcams": False,
        "allow_raise_hand": True,
        "allow_virtual_bg": True,
        "recording_features": {
            "is_allow": True,
            "is_allow_cloud": True,
            "is_allow_local": True,
            "enable_auto_cloud_recording": False,
            "only_record_admin_webcams": False,
        },
        "chat_features": {"is_allow": True, "is_allow_file_upload": True},
        "shared_note_pad_features": {"is_allow": True},
        "whiteboard_features": {"is_allow": True},
        "external_media_player_features": {"is_allow": True},
        "waiting_room_features": {"is_active": False},
        "breakout_room_features": {"is_allow": True, "allowed_number_rooms": 6},
        "display_external_link_features": {"is_allow": True},
        "polls_features": {"is_allow": True},
    }


def public_image_url(public_url: str, user_uuid: str, avatar: Optional[str]) -> Optional[str]:
    """An absolute, percent-encoded avatar URL plugNmeet's browser can fetch
    without a LearnHouse cookie, or None. LearnHouse serves user avatars
    publicly at ``/content/users/{uuid}/avatars/{file}``; absolute https
    avatars (e.g. from Google) are kept as they are."""
    if not avatar:
        return None
    avatar = avatar.strip()
    if avatar.startswith("https://"):
        parts = urlsplit(avatar)
        if not parts.hostname or parts.username or parts.password:
            return None
        return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe="/%"), parts.query, ""))
    if "/" in avatar or "\\" in avatar or avatar.startswith(".") or ":" in avatar:
        return None
    return f"{public_url}/content/users/{quote(user_uuid, safe='')}/avatars/{quote(avatar, safe='')}"


class PlugNmeetClient:
    def __init__(self, settings: PlugNmeetSettings, transport: Optional[httpx.AsyncBaseTransport] = None):
        self._settings = settings
        self._transport = transport

    async def call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {
            "Content-Type": "application/json",
            "API-KEY": self._settings.api_key,
            "HASH-SIGNATURE": sign(self._settings.api_secret, body),
        }
        url = f"{self._settings.server_url}/auth{method}"
        try:
            async with httpx.AsyncClient(
                timeout=self._settings.http_timeout,
                verify=self._settings.ca_bundle or True,
                follow_redirects=False,
                transport=self._transport,
            ) as http:
                response = await http.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PlugNmeetError(method, type(exc).__name__, unreachable=True) from exc
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PlugNmeetError(method, "response too large")
        try:
            data = response.json()
        except ValueError as exc:
            raise PlugNmeetError(method, f"HTTP {response.status_code}, not JSON", unreachable=response.status_code >= 500) from exc
        if not isinstance(data, dict):
            raise PlugNmeetError(method, "unexpected response")
        if response.status_code >= 400 or not data.get("status"):
            raise PlugNmeetError(method, str(data.get("msg") or f"HTTP {response.status_code}")[:300])
        return data

    async def is_room_active(self, room_id: str) -> bool:
        try:
            data = await self.call("/room/isRoomActive", {"room_id": room_id})
        except PlugNmeetError as exc:
            # An inactive room is reported as status false on some versions.
            if not exc.unreachable and "not active" in exc.msg.lower():
                return False
            raise
        return bool(data.get("is_active", data.get("isActive", False)))

    async def create_room(
        self,
        room_id: str,
        *,
        title: str,
        welcome_message: str,
        logout_url: str,
        webhook_url: Optional[str] = None,
    ) -> None:
        metadata: dict[str, Any] = {
            "room_title": title,
            "welcome_message": welcome_message,
            "logout_url": logout_url,
            "room_features": default_room_features(),
        }
        if webhook_url:
            metadata["webhook_url"] = webhook_url
        await self.call(
            "/room/create",
            {"room_id": room_id, "empty_timeout": self._settings.empty_timeout, "metadata": metadata},
        )

    async def get_join_token(
        self,
        room_id: str,
        *,
        name: str,
        user_id: str,
        is_admin: bool,
        profile_pic: Optional[str] = None,
    ) -> str:
        def payload(pic: Optional[str]) -> dict[str, Any]:
            metadata: dict[str, Any] = {"is_admin": is_admin}
            if pic:
                metadata["profile_pic"] = pic
            return {
                "room_id": room_id,
                "user_info": {
                    "name": name,
                    "user_id": user_id,
                    "is_admin": is_admin,
                    "is_hidden": False,
                    "user_metadata": metadata,
                },
            }

        try:
            data = await self.call("/room/getJoinToken", payload(profile_pic))
        except PlugNmeetError as exc:
            # An avatar is never worth a failed class: retry once without it.
            if not profile_pic or exc.unreachable:
                raise
            logger.info("plugNmeet refused the join with a profile picture, retrying without it")
            data = await self.call("/room/getJoinToken", payload(None))
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise PlugNmeetError("/room/getJoinToken", "no token in response")
        return token

    async def end_room(self, room_id: str) -> None:
        try:
            await self.call("/room/endRoom", {"room_id": room_id})
        except PlugNmeetError as exc:
            # Ending a room that already ended is fine.
            if not exc.unreachable and "not" in exc.msg.lower() and ("active" in exc.msg.lower() or "found" in exc.msg.lower()):
                return
            raise

    def join_url(self, token: str) -> str:
        return f"{self._settings.client_url}/?access_token={quote(token, safe='')}"
