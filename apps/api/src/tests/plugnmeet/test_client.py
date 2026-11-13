import hashlib
import hmac

import httpx
import pytest

from src.services.plugnmeet.client import (
    PlugNmeetClient,
    PlugNmeetError,
    default_room_features,
    public_image_url,
    safe_id,
    sign,
)

from .conftest import API_SECRET, PUBLIC, make_settings


def test_sign_matches_openssl_hmac():
    body = b'{"room_id":"lh-1"}'
    assert sign(API_SECRET, body) == hmac.new(API_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_safe_id():
    assert safe_id("user_ab-12 3@x.com") == "user_ab-123xcom"
    assert safe_id("a" * 100) == "a" * 64
    with pytest.raises(ValueError):
        safe_id("@@@")


def test_default_features_shape():
    features = default_room_features()
    assert features["breakout_room_features"] == {"is_allow": True, "allowed_number_rooms": 6}
    assert features["waiting_room_features"] == {"is_active": False}
    assert features["mute_on_start"] is True


@pytest.mark.parametrize(
    "avatar,expected",
    [
        (None, None),
        ("", None),
        ("avatar 1.png", f"{PUBLIC}/content/users/user_x/avatars/avatar%201.png"),
        ("https://lh3.googleusercontent.com/a/b c", "https://lh3.googleusercontent.com/a/b%20c"),
        ("https://u:p@evil.example/a.png", None),
        ("https:///nohost", None),
        ("http://plain.example/a.png", None),
        ("../secret.png", None),
        ("dir/a.png", None),
        ("a\\b.png", None),
        (".hidden", None),
    ],
)
def test_public_image_url(avatar, expected):
    assert public_image_url(PUBLIC, "user_x", avatar) == expected


async def test_signed_call_roundtrip(pnm):
    client = pnm.client(make_settings())
    assert await client.is_room_active("lh-a") is False
    await client.create_room("lh-a", title="T", welcome_message="W", logout_url=f"{PUBLIC}/x", webhook_url=f"{PUBLIC}/hook")
    assert pnm.rooms["lh-a"]["metadata"]["logout_url"] == f"{PUBLIC}/x"
    assert pnm.rooms["lh-a"]["metadata"]["webhook_url"] == f"{PUBLIC}/hook"
    assert pnm.rooms["lh-a"]["empty_timeout"] == 1800
    assert await client.is_room_active("lh-a") is True
    token = await client.get_join_token("lh-a", name="N", user_id="u1", is_admin=True, profile_pic="https://x/p.png")
    assert token == "tok-1"
    assert pnm.tokens[0]["user_info"]["user_metadata"] == {"is_admin": True, "profile_pic": "https://x/p.png"}
    await client.end_room("lh-a")
    await client.end_room("lh-a")  # already ended: fine
    assert client.join_url("a b") == "https://meet.example.com/?access_token=a%20b"


async def test_wrong_secret_is_rejected(pnm):
    client = pnm.client(make_settings(api_secret="another-secret-0123456789"))
    with pytest.raises(PlugNmeetError) as exc:
        await client.is_room_active("lh-a")
    assert not exc.value.unreachable


async def test_join_retries_without_picture(pnm):
    client = pnm.client(make_settings())
    pnm.rooms["lh-a"] = {}
    pnm.reject_profile_pic = True
    assert await client.get_join_token("lh-a", name="N", user_id="u", is_admin=False, profile_pic="https://x/p") == "tok-1"
    assert "profile_pic" not in pnm.tokens[0]["user_info"]["user_metadata"]
    with pytest.raises(PlugNmeetError):
        await client.get_join_token("lh-missing", name="N", user_id="u", is_admin=False)


async def test_join_without_token_in_response(pnm):
    pnm.overrides["/room/getJoinToken"] = lambda p: (200, {"status": True, "msg": "ok"})
    with pytest.raises(PlugNmeetError, match="no token"):
        await pnm.client(make_settings()).get_join_token("lh-a", name="N", user_id="u", is_admin=False)


async def test_is_active_reported_as_status_false(pnm):
    pnm.overrides["/room/isRoomActive"] = lambda p: (200, {"status": False, "msg": "room is not active"})
    assert await pnm.client(make_settings()).is_room_active("lh-a") is False
    pnm.overrides["/room/isRoomActive"] = lambda p: (200, {"status": False, "msg": "boom"})
    with pytest.raises(PlugNmeetError):
        await pnm.client(make_settings()).is_room_active("lh-a")
    pnm.overrides["/room/isRoomActive"] = lambda p: (200, {"status": True, "isActive": True})
    assert await pnm.client(make_settings()).is_room_active("lh-a") is True


async def test_end_room_failure(pnm):
    pnm.overrides["/room/endRoom"] = lambda p: (200, {"status": False, "msg": "permission denied"})
    with pytest.raises(PlugNmeetError):
        await pnm.client(make_settings()).end_room("lh-a")


@pytest.mark.parametrize(
    "response,unreachable",
    [
        ((502, b"<html>bad gateway</html>"), True),
        ((200, b"not json"), False),
        ((200, b"[1,2]"), False),
        ((500, {"status": False}), False),
        ((200, b"x" * (300 * 1024)), False),
    ],
)
async def test_bad_responses(pnm, response, unreachable):
    pnm.overrides["/room/create"] = lambda p: response
    with pytest.raises(PlugNmeetError) as exc:
        await pnm.client(make_settings()).create_room("lh-a", title="T", welcome_message="W", logout_url="https://x")
    assert exc.value.unreachable is unreachable


async def test_unreachable():
    def boom(request):
        raise httpx.ConnectError("refused")

    client = PlugNmeetClient(make_settings(), transport=httpx.MockTransport(boom))
    with pytest.raises(PlugNmeetError) as exc:
        await client.is_room_active("lh-a")
    assert exc.value.unreachable
    with pytest.raises(PlugNmeetError):
        await client.get_join_token("lh-a", name="N", user_id="u", is_admin=False, profile_pic="https://x/p")
    with pytest.raises(PlugNmeetError):
        await client.end_room("lh-a")
