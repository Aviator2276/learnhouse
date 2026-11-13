import json
import time

import pytest
from fastapi import HTTPException

from src.services.auth.authentik import flow


@pytest.mark.parametrize(
    "value,expected",
    [
        ("/courses?x=1", "/courses?x=1"),
        (None, "/"),
        ("", "/"),
        ("https://evil.test/", "/"),
        ("//evil.test/", "/"),
        ("/\\evil.test", "/"),
        ("/ok\r\nSet-Cookie: x", "/"),
        ("/x\x7f", "/"),
        ("relative", "/"),
        ("/" + "a" * 3000, "/"),
    ],
)
def test_safe_next_path(value, expected):
    assert flow.safe_next_path(value) == expected


def test_pkce_challenge_rfc7636_vector():
    # RFC 7636 Appendix B.
    assert (
        flow.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_round_trip_is_single_use(redis_client):
    started = flow.start_flow(org_id=3, invite_code="inv", next_path="/dash")
    assert len(started.state) >= 43 and started.code_challenge
    # Nothing secret is stored under the raw state.
    assert not redis_client.exists("authentik:flow:" + started.state)
    record = flow.consume_flow(started.state, started.binding)
    assert record is not None
    assert (record.org_id, record.invite_code, record.next_path) == (3, "inv", "/dash")
    assert flow.pkce_challenge(record.code_verifier) == started.code_challenge
    assert record.nonce == started.nonce
    # Replay.
    assert flow.consume_flow(started.state, started.binding) is None


def test_wrong_binding_burns_the_state(redis_client):
    started = flow.start_flow(org_id=None, invite_code=None, next_path=None)
    assert flow.consume_flow(started.state, "attacker-binding") is None
    assert flow.consume_flow(started.state, started.binding) is None


@pytest.mark.parametrize("state,binding", [("short", "b"), ("x" * 40 + "!", "b"), ("a" * 43, ""), ("a" * 43, "b" * 300), (None, "b")])
def test_consume_rejects_malformed_input(redis_client, state, binding):
    assert flow.consume_flow(state, binding) is None


def test_consume_rejects_corrupt_and_expired_records(redis_client):
    started = flow.start_flow(org_id=None, invite_code=None, next_path=None)
    key = "authentik:flow:" + flow._hash(started.state)
    redis_client.set(key, b"{not json")
    assert flow.consume_flow(started.state, started.binding) is None

    started = flow.start_flow(org_id=None, invite_code=None, next_path=None)
    key = "authentik:flow:" + flow._hash(started.state)
    data = json.loads(redis_client.get(key))
    data["created_at"] = time.time() - flow.FLOW_TTL_SECONDS - 5
    redis_client.set(key, json.dumps(data))
    assert flow.consume_flow(started.state, started.binding) is None


def test_redis_missing_fails_closed(monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    with pytest.raises(HTTPException) as exc:
        flow.start_flow(org_id=None, invite_code=None, next_path=None)
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException):
        flow.consume_flow("a" * 43, "b")
    with pytest.raises(HTTPException):
        flow.remember_logout_jti("j", 10)


class _Broken:
    def __getattr__(self, name):
        def fail(*a, **k):
            raise ConnectionError("redis down")

        return fail


def test_redis_errors_fail_closed(monkeypatch):
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: _Broken())
    for call in (
        lambda: flow.start_flow(org_id=None, invite_code=None, next_path=None),
        lambda: flow.consume_flow("a" * 43, "b"),
        lambda: flow.remember_logout_jti("j", 10),
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 503


def test_logout_jti_replay(redis_client):
    assert flow.remember_logout_jti("jti-1", 60) is True
    assert flow.remember_logout_jti("jti-1", 60) is False
