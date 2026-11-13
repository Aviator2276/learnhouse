"""A fake Authentik for the Authentik sign-in tests.

Real RSA/EC keys, a real JWKS and real signed JWTs; only the HTTP transport is
replaced (httpx.MockTransport), so the validation code runs exactly as it does
against a live Authentik.
"""

import json
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlsplit

import fakeredis
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from starlette.requests import Request

from src.db.user_identities import UserExternalIdentity  # noqa: F401  (registers the table)
from src.services.auth.authentik import oidc
from src.services.auth.authentik.config import AuthentikSettings

ISSUER = "https://auth.test/application/o/learnhouse/"
CLIENT_ID = "lh-client"
CLIENT_SECRET = "lh-client-secret"
REDIRECT_URI = "https://lh.test/auth/callback/authentik"


def make_settings(**overrides) -> AuthentikSettings:
    values: Dict[str, Any] = dict(
        enabled=True,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        post_logout_redirect_uri="https://lh.test/",
    )
    values.update(overrides)
    return AuthentikSettings(**values)


class FakeIdP:
    def __init__(self):
        self.rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.ec_key = ec.generate_private_key(ec.SECP256R1())
        self.kid = "rsa-1"
        self.extra_jwks: list[dict] = []
        self.discovery: Dict[str, Any] = {
            "issuer": ISSUER,
            "authorization_endpoint": "https://auth.test/application/o/authorize/",
            "token_endpoint": "https://auth.test/application/o/token/",
            "userinfo_endpoint": "https://auth.test/application/o/userinfo/",
            "jwks_uri": "https://auth.test/application/o/learnhouse/jwks/",
            "end_session_endpoint": "https://auth.test/application/o/learnhouse/end-session/",
        }
        self.nonce: Optional[str] = None
        self.sub = "ak-sub-123"
        self.claims: Dict[str, Any] = {
            "email": "alice@example.com",
            "email_verified": True,
            "preferred_username": "alice",
            "given_name": "Alice",
            "family_name": "Liddell",
            "groups": ["learners"],
        }
        self.userinfo_override: Optional[Callable[[], httpx.Response]] = None
        self.token_override: Optional[Callable[[httpx.Request], httpx.Response]] = None
        self.id_token_override: Optional[Callable[[str], str]] = None
        self.discovery_status = 200
        self.jwks_status = 200
        self.jwks_fetches = 0
        self.token_requests: list[httpx.Request] = []

    # -- keys / tokens --------------------------------------------------
    def rsa_jwk(self, kid: Optional[str] = None, **extra) -> dict:
        data = json.loads(RSAAlgorithm.to_jwk(self.rsa_key.public_key()))
        data.update({"kid": kid or self.kid, "use": "sig"})
        data.update(extra)
        return data

    def ec_jwk(self, kid: str = "ec-1") -> dict:
        data = json.loads(ECAlgorithm.to_jwk(self.ec_key.public_key()))
        data.update({"kid": kid, "use": "sig"})
        return data

    def jwks(self) -> dict:
        return {"keys": [self.rsa_jwk(), *self.extra_jwks]}

    def sign(self, claims: dict, alg: str = "RS256", kid: Optional[str] = "default", key=None, headers=None) -> str:
        hdr = dict(headers or {})
        if kid == "default":
            kid = self.kid
        if kid is not None:
            hdr["kid"] = kid
        signing_key = key if key is not None else (self.ec_key if alg.startswith("ES") else self.rsa_key)
        return jwt.encode(claims, signing_key, algorithm=alg, headers=hdr)

    def base_claims(self, **overrides) -> dict:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": self.sub,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": self.nonce,
            **self.claims,
        }
        claims.update(overrides)
        return {k: v for k, v in claims.items() if v is not None}

    def access_token(self) -> str:
        return "ak-access-token"

    def id_token(self) -> str:
        claims = self.base_claims(at_hash=oidc._b64_half_hash(self.access_token(), "RS256"))
        return self.sign(claims)

    def logout_token(self, **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "jti": f"jti-{now}-{id(overrides)}",
            "sub": self.sub,
            "events": {oidc.BACKCHANNEL_LOGOUT_EVENT: {}},
        }
        claims.update(overrides)
        return self.sign({k: v for k, v in claims.items() if v is not None})

    # -- HTTP -----------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(self.discovery_status, json=self.discovery)
        if url == self.discovery.get("jwks_uri"):
            self.jwks_fetches += 1
            return httpx.Response(self.jwks_status, json=self.jwks())
        if url == self.discovery.get("token_endpoint"):
            self.token_requests.append(request)
            if self.token_override:
                return self.token_override(request)
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["redirect_uri"] == [REDIRECT_URI]
            assert form["code_verifier"][0]
            token = self.id_token()
            if self.id_token_override:
                token = self.id_token_override(token)
            return httpx.Response(
                200,
                json={"access_token": self.access_token(), "token_type": "Bearer", "id_token": token},
            )
        if url == self.discovery.get("userinfo_endpoint"):
            if self.userinfo_override:
                return self.userinfo_override()
            assert request.headers["authorization"] == f"Bearer {self.access_token()}"
            return httpx.Response(200, json={"sub": self.sub, **self.claims})
        return httpx.Response(404)

    def capture_nonce(self, authorize_url: str) -> Dict[str, str]:
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        self.nonce = params["nonce"]
        return params


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    transport = httpx.MockTransport(fake.handler)

    def client(settings):
        return httpx.AsyncClient(transport=transport, timeout=5, follow_redirects=False)

    monkeypatch.setattr(oidc, "_client", client)
    oidc.clear_caches()
    yield fake
    oidc.clear_caches()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def redis_client(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: client)
    return client


def make_request(host: str = "lh.test", headers: Optional[dict] = None) -> Request:
    raw = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/authentik/callback",
            "headers": raw,
            "query_string": b"",
            "client": ("127.0.0.1", 50000),
            "server": (host, 443),
            "scheme": "https",
        }
    )
