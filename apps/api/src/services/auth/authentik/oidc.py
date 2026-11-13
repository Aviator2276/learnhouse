"""OpenID Connect protocol client for Authentik.

Covers discovery, JWKS, the token endpoint, ID-token and logout-token
validation and userinfo. Every function fails closed: a response that is not
exactly what the spec requires raises :class:`OIDCError`, and the caller turns
that into a generic 401 without echoing provider details to the browser.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
import jwt
from jwt import PyJWK, PyJWKSet
from jwt.exceptions import PyJWKError, PyJWTError

from src.services.auth.authentik.config import SUPPORTED_ID_TOKEN_ALGS, AuthentikSettings

logger = logging.getLogger(__name__)

DISCOVERY_TTL_SECONDS = 3600
JWKS_TTL_SECONDS = 3600
# Minimum gap between forced JWKS refreshes triggered by an unknown ``kid``,
# so a stream of forged tokens cannot turn us into an amplifier against the IdP.
JWKS_FORCED_REFRESH_INTERVAL = 60
MAX_RESPONSE_BYTES = 512 * 1024
# An ID token older than this is refused even if ``exp`` is further away.
MAX_ID_TOKEN_AGE_SECONDS = 600
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"


class OIDCError(Exception):
    """Any protocol or validation failure. The message is for logs only."""


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: Optional[str]
    end_session_endpoint: Optional[str]


_lock = threading.Lock()
_discovery_cache: Dict[str, tuple[float, ProviderMetadata]] = {}
_jwks_cache: Dict[str, tuple[float, PyJWKSet]] = {}
_jwks_last_forced: Dict[str, float] = {}


def clear_caches() -> None:
    with _lock:
        _discovery_cache.clear()
        _jwks_cache.clear()
        _jwks_last_forced.clear()


def _client(settings: AuthentikSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=settings.http_timeout,
        verify=settings.ca_bundle or True,
        follow_redirects=False,
        headers={"Accept": "application/json", "User-Agent": "LearnHouse-Authentik/1"},
    )


def _json_body(response: httpx.Response, what: str) -> Dict[str, Any]:
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise OIDCError(f"{what}: response too large")
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise OIDCError(f"{what}: response is not JSON") from exc
    if not isinstance(body, dict):
        raise OIDCError(f"{what}: response is not a JSON object")
    return body


def _require_url(value: Any, name: str, settings: AuthentikSettings) -> str:
    if not isinstance(value, str) or not value:
        raise OIDCError(f"discovery: {name} missing")
    if not value.startswith("https://") and not (
        settings.allow_insecure_http and value.startswith("http://")
    ):
        raise OIDCError(f"discovery: {name} is not https")
    return value


async def get_metadata(settings: AuthentikSettings) -> ProviderMetadata:
    now = time.monotonic()
    cached = _discovery_cache.get(settings.issuer)
    if cached and now - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]

    url = settings.issuer_normalized + "/.well-known/openid-configuration"
    try:
        async with _client(settings) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise OIDCError(f"discovery: request failed ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise OIDCError(f"discovery: HTTP {response.status_code}")
    doc = _json_body(response, "discovery")

    # OIDC Discovery 1.0 §4.3: the issuer in the document MUST be identical to
    # the one we were configured with. This is what stops a mix-up attack.
    if not isinstance(doc.get("issuer"), str) or doc["issuer"].rstrip("/") != settings.issuer_normalized:
        raise OIDCError("discovery: issuer mismatch")

    optional_userinfo = doc.get("userinfo_endpoint")
    optional_end_session = doc.get("end_session_endpoint")
    metadata = ProviderMetadata(
        issuer=doc["issuer"],
        authorization_endpoint=_require_url(doc.get("authorization_endpoint"), "authorization_endpoint", settings),
        token_endpoint=_require_url(doc.get("token_endpoint"), "token_endpoint", settings),
        jwks_uri=_require_url(doc.get("jwks_uri"), "jwks_uri", settings),
        userinfo_endpoint=(
            _require_url(optional_userinfo, "userinfo_endpoint", settings) if optional_userinfo else None
        ),
        end_session_endpoint=(
            _require_url(optional_end_session, "end_session_endpoint", settings)
            if optional_end_session
            else None
        ),
    )
    with _lock:
        _discovery_cache[settings.issuer] = (now, metadata)
    return metadata


async def _fetch_jwks(settings: AuthentikSettings, metadata: ProviderMetadata) -> PyJWKSet:
    try:
        async with _client(settings) as client:
            response = await client.get(metadata.jwks_uri)
    except httpx.HTTPError as exc:
        raise OIDCError(f"jwks: request failed ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise OIDCError(f"jwks: HTTP {response.status_code}")
    body = _json_body(response, "jwks")
    try:
        jwks = PyJWKSet.from_dict(body)
    except (PyJWKError, PyJWTError, KeyError, TypeError, ValueError) as exc:
        raise OIDCError("jwks: unusable key set") from exc
    with _lock:
        _jwks_cache[metadata.jwks_uri] = (time.monotonic(), jwks)
    return jwks


_EC_CURVE_FOR_ALG = {"ES256": "P-256", "ES384": "P-384", "ES512": "P-521"}


def _key_fits_alg(key: PyJWK, alg: str) -> bool:
    data = key._jwk_data
    declared = data.get("alg")
    if declared is not None and declared != alg:
        return False
    kty = data.get("kty")
    if alg.startswith(("RS", "PS")):
        return kty == "RSA"
    return kty == "EC" and data.get("crv") == _EC_CURVE_FOR_ALG.get(alg)


async def _signing_key(settings: AuthentikSettings, metadata: ProviderMetadata, kid: Optional[str], alg: str) -> PyJWK:
    now = time.monotonic()
    cached = _jwks_cache.get(metadata.jwks_uri)
    jwks = cached[1] if cached and now - cached[0] < JWKS_TTL_SECONDS else await _fetch_jwks(settings, metadata)

    def pick(keyset: PyJWKSet) -> Optional[PyJWK]:
        candidates = [
            k
            for k in keyset.keys
            if (kid is None or k.key_id == kid)
            and k.public_key_use in (None, "sig")
            and _key_fits_alg(k, alg)
        ]
        # Without a kid the choice must be unambiguous.
        if kid is None and len(candidates) != 1:
            return None
        if not candidates:
            return None
        try:
            # Rebuild the key for the header's algorithm (a JWK without "alg"
            # defaults to RS256/ES256 in PyJWT, which would reject RS512 etc.).
            return PyJWK(dict(candidates[0]._jwk_data), alg)
        except (PyJWKError, PyJWTError, ValueError, TypeError):
            return None

    key = pick(jwks)
    if key is None and kid is not None:
        # Key rotation: refresh once, but not more than once a minute.
        last = _jwks_last_forced.get(metadata.jwks_uri, 0.0)
        if now - last >= JWKS_FORCED_REFRESH_INTERVAL:
            with _lock:
                _jwks_last_forced[metadata.jwks_uri] = now
            key = pick(await _fetch_jwks(settings, metadata))
    if key is None:
        raise OIDCError("jwt: no matching signing key")
    return key


async def _decode_signed_jwt(
    token: str,
    settings: AuthentikSettings,
    metadata: ProviderMetadata,
    required: list[str],
) -> Dict[str, Any]:
    if not isinstance(token, str) or token.count(".") != 2:
        raise OIDCError("jwt: malformed")
    try:
        header = jwt.get_unverified_header(token)
    except PyJWTError as exc:
        raise OIDCError("jwt: malformed header") from exc
    alg = header.get("alg")
    if alg not in SUPPORTED_ID_TOKEN_ALGS:
        raise OIDCError("jwt: algorithm not allowed")
    kid = header.get("kid")  # PyJWT already guarantees str or None
    key = await _signing_key(settings, metadata, kid, alg)
    try:
        return jwt.decode(
            token,
            key=key.key,
            algorithms=[alg],
            audience=settings.client_id,
            issuer=metadata.issuer,
            leeway=settings.clock_skew_seconds,
            options={"require": required, "verify_signature": True},
        )
    except PyJWTError as exc:
        raise OIDCError(f"jwt: {type(exc).__name__}") from exc


def _b64_half_hash(value: str, alg: str) -> str:
    bits = alg[-3:]
    digest = {"256": hashlib.sha256, "384": hashlib.sha384, "512": hashlib.sha512}[bits](
        value.encode("ascii")
    ).digest()
    return base64.urlsafe_b64encode(digest[: len(digest) // 2]).rstrip(b"=").decode("ascii")


def _check_audience_and_azp(claims: Dict[str, Any], client_id: str) -> None:
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    azp = claims.get("azp")
    # OIDC Core §3.1.3.7 (3)-(5): with several audiences azp is required, and
    # whenever azp is present it must be us.
    if len(audiences) > 1 and azp is None:
        raise OIDCError("id_token: azp required for multiple audiences")
    if azp is not None and azp != client_id:
        raise OIDCError("id_token: azp mismatch")


async def validate_id_token(
    id_token: str,
    *,
    settings: AuthentikSettings,
    metadata: ProviderMetadata,
    nonce: str,
    access_token: Optional[str] = None,
) -> Dict[str, Any]:
    claims = await _decode_signed_jwt(
        id_token, settings, metadata, required=["iss", "sub", "aud", "exp", "iat"]
    )
    _check_audience_and_azp(claims, settings.client_id)

    token_nonce = claims.get("nonce")
    if not isinstance(token_nonce, str) or not hmac.compare_digest(token_nonce, nonce):
        raise OIDCError("id_token: nonce mismatch")

    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or time.time() - iat > MAX_ID_TOKEN_AGE_SECONDS + settings.clock_skew_seconds:
        raise OIDCError("id_token: too old")

    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 255:
        raise OIDCError("id_token: bad sub")

    at_hash = claims.get("at_hash")
    if at_hash is not None and access_token is not None:
        alg = jwt.get_unverified_header(id_token)["alg"]
        if not isinstance(at_hash, str) or not hmac.compare_digest(at_hash, _b64_half_hash(access_token, alg)):
            raise OIDCError("id_token: at_hash mismatch")
    return claims


async def exchange_code(
    *,
    settings: AuthentikSettings,
    metadata: ProviderMetadata,
    code: str,
    code_verifier: str,
) -> Dict[str, Any]:
    try:
        async with _client(settings) as client:
            response = await client.post(
                metadata.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.redirect_uri,
                    "code_verifier": code_verifier,
                },
                # client_secret_basic (RFC 6749 §2.3.1): form-encode, then basic.
                auth=httpx.BasicAuth(settings.client_id, settings.client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise OIDCError(f"token: request failed ({type(exc).__name__})") from exc
    if response.status_code != 200:
        # Log the error code only; error_description can contain user data.
        err = None
        try:
            err = response.json().get("error")
        except Exception:
            pass
        raise OIDCError(f"token: HTTP {response.status_code} error={err!r}")
    body = _json_body(response, "token")
    if not isinstance(body.get("id_token"), str):
        raise OIDCError("token: no id_token")
    if not isinstance(body.get("access_token"), str):
        raise OIDCError("token: no access_token")
    if str(body.get("token_type", "")).lower() != "bearer":
        raise OIDCError("token: unexpected token_type")
    return body


async def fetch_userinfo(
    *,
    settings: AuthentikSettings,
    metadata: ProviderMetadata,
    access_token: str,
    expected_sub: str,
) -> Dict[str, Any]:
    if not metadata.userinfo_endpoint:
        return {}
    try:
        async with _client(settings) as client:
            response = await client.get(
                metadata.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise OIDCError(f"userinfo: request failed ({type(exc).__name__})") from exc
    if response.status_code != 200:
        raise OIDCError(f"userinfo: HTTP {response.status_code}")
    if "application/jwt" in response.headers.get("content-type", ""):
        raise OIDCError("userinfo: signed responses are not supported")
    body = _json_body(response, "userinfo")
    # OIDC Core §5.3.2: the sub in userinfo MUST match the ID token's.
    if body.get("sub") != expected_sub:
        raise OIDCError("userinfo: sub mismatch")
    return body


async def validate_logout_token(
    logout_token: str, *, settings: AuthentikSettings, metadata: ProviderMetadata
) -> Dict[str, Any]:
    """OpenID Connect Back-Channel Logout 1.0 §2.6."""
    claims = await _decode_signed_jwt(
        logout_token, settings, metadata, required=["iss", "aud", "iat", "jti", "events"]
    )
    events = claims.get("events")
    if not isinstance(events, dict) or BACKCHANNEL_LOGOUT_EVENT not in events:
        raise OIDCError("logout_token: missing logout event")
    if not isinstance(events[BACKCHANNEL_LOGOUT_EVENT], dict):
        raise OIDCError("logout_token: logout event must be an object")
    if "nonce" in claims:
        raise OIDCError("logout_token: nonce is forbidden")
    if not isinstance(claims.get("sub"), str) and not isinstance(claims.get("sid"), str):
        raise OIDCError("logout_token: sub or sid required")
    if not isinstance(claims.get("jti"), str) or not claims["jti"] or len(claims["jti"]) > 255:
        raise OIDCError("logout_token: bad jti")
    iat = claims.get("iat")
    if not isinstance(iat, (int, float)) or abs(time.time() - iat) > MAX_ID_TOKEN_AGE_SECONDS:
        raise OIDCError("logout_token: stale")
    _check_audience_and_azp(claims, settings.client_id)
    return claims


def verified_email(claims: Dict[str, Any]) -> Optional[str]:
    """The lower-cased email, only when the IdP explicitly marks it verified.

    Accepts a JSON boolean ``true`` or the string ``"true"``; anything else
    (missing, ``"false"``, ``1``...) is unverified. Same rule as the Google path.
    """
    email = claims.get("email")
    flag = claims.get("email_verified")
    is_verified = flag.strip().lower() == "true" if isinstance(flag, str) else flag is True
    if not isinstance(email, str) or "@" not in email or not is_verified:
        return None
    email = email.strip().lower()
    return email if 3 <= len(email) <= 320 else None
