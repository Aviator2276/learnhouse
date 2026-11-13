import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from src.services.auth.authentik import oidc
from src.services.auth.authentik.oidc import OIDCError

from .conftest import CLIENT_ID, ISSUER, make_settings


async def _meta(settings):
    return await oidc.get_metadata(settings)


# -- discovery -------------------------------------------------------------


async def test_discovery_is_cached(idp, settings):
    calls = {"n": 0}
    original = idp.handler

    def counting(request):
        if "openid-configuration" in str(request.url):
            calls["n"] += 1
        return original(request)

    idp.handler = counting  # type: ignore[method-assign]
    transport = httpx.MockTransport(counting)
    oidc._client = lambda s: httpx.AsyncClient(transport=transport)  # type: ignore[assignment]
    first = await _meta(settings)
    second = await _meta(settings)
    assert first is second and calls["n"] == 1
    assert first.end_session_endpoint and first.userinfo_endpoint


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(issuer="https://evil.test/application/o/learnhouse/"),
        lambda d: d.pop("issuer"),
        lambda d: d.update(token_endpoint="http://auth.test/token/"),
        lambda d: d.pop("jwks_uri"),
        lambda d: d.update(userinfo_endpoint="http://auth.test/userinfo/"),
        lambda d: d.update(end_session_endpoint=""),
    ],
)
async def test_discovery_rejects_bad_documents(idp, settings, mutate):
    mutate(idp.discovery)
    if idp.discovery.get("end_session_endpoint") == "":
        # An empty optional endpoint is simply "not supported".
        assert (await _meta(settings)).end_session_endpoint is None
        return
    with pytest.raises(OIDCError):
        await _meta(settings)


async def test_discovery_optional_endpoints_absent(idp, settings):
    idp.discovery.pop("userinfo_endpoint")
    idp.discovery.pop("end_session_endpoint")
    meta = await _meta(settings)
    assert meta.userinfo_endpoint is None and meta.end_session_endpoint is None


async def test_discovery_http_allowed_when_opted_in(idp):
    idp.discovery["token_endpoint"] = "http://auth.test/token/"
    meta = await _meta(make_settings(allow_insecure_http=True))
    assert meta.token_endpoint.startswith("http://")


async def test_discovery_http_error(idp, settings):
    idp.discovery_status = 500
    with pytest.raises(OIDCError, match="HTTP 500"):
        await _meta(settings)


async def test_discovery_network_error(monkeypatch, settings):
    def boom(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(oidc, "_client", lambda s: httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    oidc.clear_caches()
    with pytest.raises(OIDCError, match="request failed"):
        await _meta(settings)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=["a", "list"]),
        httpx.Response(200, content=b"{" + b" " * (oidc.MAX_RESPONSE_BYTES + 1) + b"}"),
    ],
)
async def test_discovery_bad_bodies(monkeypatch, settings, response):
    monkeypatch.setattr(
        oidc, "_client", lambda s: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: response))
    )
    oidc.clear_caches()
    with pytest.raises(OIDCError):
        await _meta(settings)


def test_real_client_honours_settings(tmp_path):
    client = oidc._client(make_settings())
    assert client.follow_redirects is False
    assert client.timeout.connect == 10.0


# -- ID token --------------------------------------------------------------


async def _validate(idp, settings, token, nonce="n-1", access_token=None):
    return await oidc.validate_id_token(
        token, settings=settings, metadata=await _meta(settings), nonce=nonce, access_token=access_token
    )


async def test_valid_id_token(idp, settings):
    idp.nonce = "n-1"
    claims = await _validate(idp, settings, idp.id_token(), access_token=idp.access_token())
    assert claims["sub"] == idp.sub


async def test_valid_ec_id_token_without_kid_single_key(idp, settings):
    idp.nonce = "n-1"
    idp.kid = "unused"
    idp.jwks = lambda: {"keys": [idp.ec_jwk()]}  # type: ignore[method-assign]
    token = idp.sign(idp.base_claims(), alg="ES256", kid=None)
    assert (await _validate(idp, settings, token))["sub"] == idp.sub


async def test_rs512_with_alg_less_jwk(idp, settings):
    idp.nonce = "n-1"
    token = idp.sign(idp.base_claims(), alg="RS512")
    assert (await _validate(idp, settings, token))["sub"] == idp.sub


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"iss": "https://evil.test/"}, "InvalidIssuer"),
        ({"aud": "someone-else"}, "InvalidAudience"),
        ({"exp": int(time.time()) - 3600}, "ExpiredSignature"),
        ({"nonce": "wrong"}, "nonce"),
        ({"nonce": None}, "nonce"),
        ({"iat": int(time.time()) - 7200}, "too old"),
        ({"sub": ""}, "bad sub"),
        ({"sub": "x" * 300}, "bad sub"),
        ({"aud": [CLIENT_ID, "other"]}, "azp required"),
        ({"aud": [CLIENT_ID, "other"], "azp": "other"}, "azp mismatch"),
    ],
)
async def test_id_token_claim_checks(idp, settings, overrides, match):
    idp.nonce = "n-1"
    token = idp.sign(idp.base_claims(**overrides))
    with pytest.raises(OIDCError, match=match):
        await _validate(idp, settings, token)


async def test_id_token_multi_audience_with_azp_ok(idp, settings):
    idp.nonce = "n-1"
    token = idp.sign(idp.base_claims(aud=[CLIENT_ID, "other"], azp=CLIENT_ID))
    assert (await _validate(idp, settings, token))["azp"] == CLIENT_ID


async def test_id_token_at_hash_mismatch(idp, settings):
    idp.nonce = "n-1"
    token = idp.sign(idp.base_claims(at_hash="AAAA"))
    with pytest.raises(OIDCError, match="at_hash"):
        await _validate(idp, settings, token, access_token="different")


async def test_id_token_signed_by_unknown_key(idp, settings):
    idp.nonce = "n-1"
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = idp.sign(idp.base_claims(), key=other)
    with pytest.raises(OIDCError, match="InvalidSignature"):
        await _validate(idp, settings, token)


@pytest.mark.parametrize("alg", ["HS256", "none"])
async def test_id_token_symmetric_or_none_alg_rejected(idp, settings, alg):
    idp.nonce = "n-1"
    if alg == "none":
        token = jwt.encode(idp.base_claims(), None, algorithm="none")
    else:
        token = jwt.encode(idp.base_claims(), CLIENT_ID * 4, algorithm="HS256", headers={"kid": idp.kid})
    with pytest.raises(OIDCError, match="algorithm not allowed"):
        await _validate(idp, settings, token)


@pytest.mark.parametrize("token", ["", "a.b", "not.a.jwt", 12345])
async def test_malformed_tokens(idp, settings, token):
    with pytest.raises(OIDCError):
        await _validate(idp, settings, token)


async def test_crit_header_rejected(idp, settings):
    idp.nonce = "n-1"
    # PyJWT refuses unknown critical extensions while parsing the header.
    with pytest.raises(OIDCError, match="malformed header"):
        await _validate(idp, settings, idp.sign(idp.base_claims(), headers={"crit": ["exp"]}))


async def test_key_rotation_refreshes_jwks_once(idp, settings, monkeypatch):
    idp.nonce = "n-1"
    await _validate(idp, settings, idp.id_token())
    assert idp.jwks_fetches == 1
    # Authentik rotates its signing key: new kid, not yet in our cache.
    idp.kid = "rsa-2"
    await _validate(idp, settings, idp.id_token())
    assert idp.jwks_fetches == 2
    # An unknown kid right after a forced refresh does not refetch again.
    with pytest.raises(OIDCError, match="no matching"):
        await _validate(idp, settings, idp.sign(idp.base_claims(), kid="rsa-unknown"))
    assert idp.jwks_fetches == 2


async def test_jwks_key_alg_and_use_filters(idp, settings):
    idp.nonce = "n-1"
    idp.jwks = lambda: {  # type: ignore[method-assign]
        "keys": [idp.rsa_jwk(alg="RS512"), idp.rsa_jwk(kid="enc", use="enc")]
    }
    with pytest.raises(OIDCError, match="no matching"):
        await _validate(idp, settings, idp.id_token())
    with pytest.raises(OIDCError, match="no matching"):
        await _validate(idp, settings, idp.sign(idp.base_claims(), kid="enc"))


async def test_jwks_ambiguous_without_kid(idp, settings):
    idp.nonce = "n-1"
    idp.extra_jwks = [idp.rsa_jwk(kid="rsa-other")]
    with pytest.raises(OIDCError, match="no matching"):
        await _validate(idp, settings, idp.sign(idp.base_claims(), kid=None))


def test_jwks_curve_and_type_filter(idp):
    from jwt import PyJWK

    from src.services.auth.authentik.oidc import _key_fits_alg

    # A P-256 key can never verify an ES384 token, nor an RSA key an ES token.
    assert not _key_fits_alg(PyJWK(idp.ec_jwk()), "ES384")
    assert _key_fits_alg(PyJWK(idp.ec_jwk()), "ES256")
    assert not _key_fits_alg(PyJWK(idp.rsa_jwk()), "ES256")


async def test_jwks_unusable_key_rebuild(idp, settings, monkeypatch):
    idp.nonce = "n-1"
    from jwt import PyJWK

    real = PyJWK

    def flaky(data, alg=None):
        if alg is not None:
            raise ValueError("cannot rebuild")
        return real(data, alg)

    monkeypatch.setattr(oidc, "PyJWK", flaky)
    with pytest.raises(OIDCError, match="no matching"):
        await _validate(idp, settings, idp.id_token())


async def test_jwks_http_errors(idp, settings):
    idp.nonce = "n-1"
    idp.jwks_status = 503
    with pytest.raises(OIDCError, match="jwks: HTTP 503"):
        await _validate(idp, settings, idp.id_token())
    idp.jwks_status = 200
    idp.jwks = lambda: {"keys": [{"kty": "oct", "k": "abc"}]}  # type: ignore[method-assign]
    oidc.clear_caches()
    with pytest.raises(OIDCError):
        await _validate(idp, settings, idp.id_token())
    idp.jwks = lambda: {"keys": "nope"}  # type: ignore[method-assign]
    oidc.clear_caches()
    with pytest.raises(OIDCError, match="unusable"):
        await _validate(idp, settings, idp.id_token())


async def test_jwks_network_error(idp, settings, monkeypatch):
    meta = await _meta(settings)

    def boom(request):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(oidc, "_client", lambda s: httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    with pytest.raises(OIDCError, match="jwks: request failed"):
        await oidc._fetch_jwks(settings, meta)


def test_half_hash_variants():
    assert len(oidc._b64_half_hash("tok", "RS256")) == 22
    assert len(oidc._b64_half_hash("tok", "ES384")) == 32
    assert len(oidc._b64_half_hash("tok", "PS512")) == 43


# -- token endpoint ----------------------------------------------------------


async def test_exchange_code_uses_basic_auth_and_pkce(idp, settings):
    idp.nonce = "n"
    body = await oidc.exchange_code(settings=settings, metadata=await _meta(settings), code="c", code_verifier="v" * 43)
    req = idp.token_requests[0]
    assert req.headers["authorization"].startswith("Basic ")
    assert b"client_secret" not in req.content
    assert body["access_token"] == idp.access_token()


@pytest.mark.parametrize(
    "response,match",
    [
        (httpx.Response(400, json={"error": "invalid_grant", "error_description": "secret detail"}), "invalid_grant"),
        (httpx.Response(500, content=b"oops"), "HTTP 500"),
        (httpx.Response(200, json={"access_token": "a", "token_type": "Bearer"}), "no id_token"),
        (httpx.Response(200, json={"id_token": "a.b.c", "token_type": "Bearer"}), "no access_token"),
        (httpx.Response(200, json={"id_token": "a.b.c", "access_token": "a", "token_type": "mac"}), "token_type"),
    ],
)
async def test_exchange_code_failures(idp, settings, response, match):
    idp.token_override = lambda request: response
    with pytest.raises(OIDCError, match=match) as exc:
        await oidc.exchange_code(settings=settings, metadata=await _meta(settings), code="c", code_verifier="v")
    assert "secret detail" not in str(exc.value)


async def test_exchange_code_network_error(idp, settings):
    def boom(request):
        raise httpx.ConnectError("down")

    idp.token_override = boom
    with pytest.raises(OIDCError, match="request failed"):
        await oidc.exchange_code(settings=settings, metadata=await _meta(settings), code="c", code_verifier="v")


# -- userinfo ----------------------------------------------------------------


async def test_userinfo_ok_and_sub_checked(idp, settings):
    meta = await _meta(settings)
    info = await oidc.fetch_userinfo(settings=settings, metadata=meta, access_token=idp.access_token(), expected_sub=idp.sub)
    assert info["groups"] == ["learners"]
    with pytest.raises(OIDCError, match="sub mismatch"):
        await oidc.fetch_userinfo(settings=settings, metadata=meta, access_token=idp.access_token(), expected_sub="other")


@pytest.mark.parametrize(
    "response,match",
    [
        (httpx.Response(401), "HTTP 401"),
        (httpx.Response(200, content=b"x.y.z", headers={"content-type": "application/jwt"}), "signed"),
    ],
)
async def test_userinfo_failures(idp, settings, response, match):
    idp.userinfo_override = lambda: response
    with pytest.raises(OIDCError, match=match):
        await oidc.fetch_userinfo(settings=settings, metadata=await _meta(settings), access_token="a", expected_sub="s")


async def test_userinfo_network_error_and_absent_endpoint(idp, settings):
    def boom():
        raise httpx.ConnectError("down")

    idp.userinfo_override = boom
    meta = await _meta(settings)
    with pytest.raises(OIDCError, match="request failed"):
        await oidc.fetch_userinfo(settings=settings, metadata=meta, access_token="a", expected_sub="s")
    from dataclasses import replace

    assert await oidc.fetch_userinfo(
        settings=settings, metadata=replace(meta, userinfo_endpoint=None), access_token="a", expected_sub="s"
    ) == {}


# -- back-channel logout token ----------------------------------------------


async def test_logout_token_valid(idp, settings):
    claims = await oidc.validate_logout_token(idp.logout_token(), settings=settings, metadata=await _meta(settings))
    assert claims["sub"] == idp.sub


@pytest.mark.parametrize(
    "overrides,match",
    [
        ({"events": {"other": {}}}, "missing logout event"),
        ({"events": {oidc.BACKCHANNEL_LOGOUT_EVENT: "x"}}, "must be an object"),
        ({"nonce": "n"}, "nonce is forbidden"),
        ({"sub": None}, "sub or sid"),
        ({"jti": ""}, "bad jti"),
        ({"iat": int(time.time()) - 3600}, "stale"),
        ({"aud": [CLIENT_ID, "x"]}, "azp required"),
        ({"events": None}, "MissingRequiredClaim"),
    ],
)
async def test_logout_token_rejections(idp, settings, overrides, match):
    with pytest.raises(OIDCError, match=match):
        await oidc.validate_logout_token(
            idp.logout_token(**overrides), settings=settings, metadata=await _meta(settings)
        )


async def test_logout_token_sid_only_ok(idp, settings):
    token = idp.logout_token(sub=None, sid="sess-1")
    claims = await oidc.validate_logout_token(token, settings=settings, metadata=await _meta(settings))
    assert claims["sid"] == "sess-1"


# -- email -------------------------------------------------------------------


@pytest.mark.parametrize(
    "claims,expected",
    [
        ({"email": " Alice@Example.COM ", "email_verified": True}, "alice@example.com"),
        ({"email": "a@b.c", "email_verified": "TRUE"}, "a@b.c"),
        ({"email": "a@b.c", "email_verified": "false"}, None),
        ({"email": "a@b.c", "email_verified": 1}, None),
        ({"email": "a@b.c"}, None),
        ({"email": "no-at", "email_verified": True}, None),
        ({"email": 5, "email_verified": True}, None),
        ({"email": "a@" + "b" * 400, "email_verified": True}, None),
    ],
)
def test_verified_email(claims, expected):
    assert oidc.verified_email(claims) == expected


def test_issuer_constant():
    assert ISSUER.endswith("/")
