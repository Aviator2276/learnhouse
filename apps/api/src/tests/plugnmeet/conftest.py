"""A fake plugNmeet server and fixtures for the live-class tests.

The fake verifies every request exactly like plugNmeet does (API-KEY header,
hex HMAC-SHA256 of the raw body), so a signing bug fails the tests instead of
only failing against the real server.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

import fakeredis
import httpx
import jwt
import pytest
from starlette.requests import Request

from src.db.live_classes import LiveClass, LiveClassAttendance  # noqa: F401  (registers the tables)
from src.db.roles import Role, RoleTypeEnum
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.routers import plugnmeet as router
from src.services.plugnmeet.client import PlugNmeetClient
from src.services.plugnmeet.config import PlugNmeetSettings

API_KEY = "plugnmeet"
API_SECRET = "test-secret-0123456789abcdef0123456789"
SERVER = "http://192.168.1.20:8080"
CLIENT = "https://meet.example.com"
PUBLIC = "https://learn.example.com"


def make_settings(**overrides) -> PlugNmeetSettings:
    values: dict[str, Any] = dict(
        enabled=True,
        server_url=SERVER,
        client_url=CLIENT,
        api_key=API_KEY,
        api_secret=API_SECRET,
        public_url=PUBLIC,
        quick_room=True,
        webhook_enabled=True,
    )
    values.update(overrides)
    return PlugNmeetSettings(**values)


class FakePlugNmeet:
    def __init__(self) -> None:
        self.rooms: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.tokens: list[dict] = []
        # path -> callable(payload) returning (status, json) to override.
        self.overrides: dict[str, Any] = {}
        self.reject_profile_pic = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        expected = hmac.new(API_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if request.headers.get("API-KEY") != API_KEY or request.headers.get("HASH-SIGNATURE") != expected:
            return httpx.Response(401, json={"status": False, "msg": "invalid signature"})
        if request.headers.get("content-type") != "application/json":
            return httpx.Response(400, json={"status": False, "msg": "bad content type"})
        payload = json.loads(body)
        path = request.url.path.removeprefix("/auth")
        self.calls.append((path, payload))
        if path in self.overrides:
            code, data = self.overrides[path](payload)
            return httpx.Response(code, json=data) if not isinstance(data, (str, bytes)) else httpx.Response(code, content=data)
        room_id = payload.get("room_id")
        if path == "/room/isRoomActive":
            return httpx.Response(200, json={"status": True, "msg": "ok", "is_active": room_id in self.rooms})
        if path == "/room/create":
            self.rooms[room_id] = payload
            return httpx.Response(200, json={"status": True, "msg": "created"})
        if path == "/room/getJoinToken":
            if room_id not in self.rooms:
                return httpx.Response(200, json={"status": False, "msg": "room not active"})
            if self.reject_profile_pic and "profile_pic" in payload["user_info"]["user_metadata"]:
                return httpx.Response(200, json={"status": False, "msg": "profile_pic: must be a valid URI"})
            self.tokens.append(payload)
            return httpx.Response(200, json={"status": True, "msg": "ok", "token": f"tok-{len(self.tokens)}"})
        if path == "/room/endRoom":
            if room_id not in self.rooms:
                return httpx.Response(200, json={"status": False, "msg": "room not active"})
            del self.rooms[room_id]
            return httpx.Response(200, json={"status": True, "msg": "ended"})
        return httpx.Response(404, json={"status": False, "msg": "unknown"})

    def client(self, settings: PlugNmeetSettings) -> PlugNmeetClient:
        return PlugNmeetClient(settings, transport=httpx.MockTransport(self.handler))


@pytest.fixture
def redis_client(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: client)
    return client


@pytest.fixture
def pnm():
    return FakePlugNmeet()


@pytest.fixture
def env(monkeypatch, pnm, redis_client):
    holder = SimpleNamespace(settings=make_settings(), pnm=pnm, redis=redis_client)
    monkeypatch.setattr(router, "get_plugnmeet_settings", lambda: holder.settings)
    monkeypatch.setattr("src.services.plugnmeet.calendar_source.get_plugnmeet_settings", lambda: holder.settings)
    monkeypatch.setattr(router, "make_client", lambda settings: pnm.client(settings))
    monkeypatch.setattr(router, "check_rate_limit", lambda key, n, window: (True, 1, 0))
    return holder


def make_request(path: str = "/api/v1/plugnmeet", headers: Optional[dict] = None, body: bytes = b"") -> Request:
    raw = [(b"host", b"learn.example.com")] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": raw,
            "query_string": b"",
            "client": ("203.0.113.9", 1234),
            "server": ("learn.example.com", 443),
            "scheme": "https",
        },
        receive,
    )


async def add_member(db, org, *, user_id: int, uuid: str, role_id: int, username: Optional[str] = None, avatar: str = "") -> PublicUser:
    user = User(
        id=user_id,
        username=username or uuid,
        first_name=username or "",
        last_name="",
        email=f"{uuid}@test.com",
        password="x",
        user_uuid=uuid,
        avatar_image=avatar,
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(user)
    await db.commit()
    if role_id:
        db.add(UserOrganization(user_id=user_id, org_id=org.id, role_id=role_id,
                                creation_date=str(datetime.now()), update_date=str(datetime.now())))
        await db.commit()
    return PublicUser(id=user.id, username=user.username, first_name=user.first_name, last_name="",
                      email=user.email, user_uuid=uuid)


async def maintainer_role(db, org) -> Role:
    from src.tests.conftest import ADMIN_RIGHTS

    role = Role(id=2, name="Maintainer", org_id=org.id, role_type=RoleTypeEnum.TYPE_ORGANIZATION,
                role_uuid="role_maintainer", rights=ADMIN_RIGHTS.model_dump(),
                creation_date=str(datetime.now()), update_date=str(datetime.now()))
    db.add(role)
    await db.commit()
    return role


def webhook_token(body: bytes, *, secret: str = API_SECRET, iss: str = API_KEY, exp_in: int = 300, sha: Optional[str] = None, **extra) -> str:
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode() if sha is None else sha
    claims = {"iss": iss, "exp": int(time.time()) + exp_in, "nbf": int(time.time()) - 5, "sha256": digest, **extra}
    return jwt.encode(claims, secret, algorithm="HS256")
