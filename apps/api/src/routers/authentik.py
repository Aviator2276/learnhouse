"""Authentik (OpenID Connect) sign-in endpoints, mounted at ``/auth/authentik``.

The web app reaches these through its ``/api/auth/*`` proxy, which refuses
cross-site POSTs and turns the token-bearing callback response into the usual
httpOnly session cookies. See ``src/services/auth/authentik/__init__.py``.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.users import UserRead
from src.security.session_context import AUTH_METHOD_AUTHENTIK
from src.services.auth.authentik import flow as flow_store
from src.services.auth.authentik import identity, oidc
from src.services.auth.authentik.config import AuthentikSettings, get_authentik_settings
from src.services.security.rate_limiting import check_login_rate_limit, get_client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

GENERIC_FAILURE = "Authentik sign-in failed. Please try again."
SID_TTL_SECONDS = 30 * 24 * 3600


class AuthorizeBody(BaseModel):
    org_id: Optional[int] = Field(default=None, ge=1)
    invite_code: Optional[str] = Field(default=None, max_length=128)
    next: Optional[str] = Field(default=None, max_length=2048)


class CallbackBody(BaseModel):
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=1, max_length=256)
    binding: str = Field(min_length=1, max_length=256)
    iss: Optional[str] = Field(default=None, max_length=2048)


def _settings_or_404() -> AuthentikSettings:
    settings = get_authentik_settings()
    if not settings.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Authentik sign-in is not enabled")
    return settings


def _rate_limit(request: Request) -> None:
    allowed, retry_after = check_login_rate_limit(request)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "message": "Too many sign-in attempts. Please try again later.",
                "retry_after": retry_after,
            },
        )


async def _metadata(settings: AuthentikSettings) -> oidc.ProviderMetadata:
    try:
        return await oidc.get_metadata(settings)
    except oidc.OIDCError as exc:
        logger.error("Authentik discovery failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentik is unreachable right now, please try again.",
        ) from exc


async def _org_or_400(db: AsyncSession, org_id: Optional[int]) -> Optional[Organization]:
    if org_id is None:
        return None
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalars().first()
    if org is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid org_id")
    return org


@router.get("/config", summary="Whether Authentik sign-in is available")
async def authentik_config():
    settings = get_authentik_settings()
    if not settings.enabled:
        return {"enabled": False}
    return {"enabled": True, "display_name": settings.display_name}


@router.post("/authorize", summary="Start an Authentik sign-in")
async def authentik_authorize(
    request: Request,
    body: AuthorizeBody,
    db_session: AsyncSession = Depends(get_db_session),
):
    settings = _settings_or_404()
    _rate_limit(request)
    org = await _org_or_400(db_session, body.org_id)
    if org is not None:
        from src.services.orgs.auth_policy import enforce_login_auth_method

        await enforce_login_auth_method(db_session, org.id, AUTH_METHOD_AUTHENTIK)

    metadata = await _metadata(settings)
    started = flow_store.start_flow(org_id=body.org_id, invite_code=body.invite_code, next_path=body.next)
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": " ".join(settings.scopes),
        "state": started.state,
        "nonce": started.nonce,
        "code_challenge": started.code_challenge,
        "code_challenge_method": "S256",
    }
    sep = "&" if "?" in metadata.authorization_endpoint else "?"
    return {
        "url": metadata.authorization_endpoint + sep + urlencode(params),
        "state": started.state,
        "binding": started.binding,
    }


def _remember_sid(sid: Optional[str], user_id: int) -> None:
    if not isinstance(sid, str) or not sid or len(sid) > 255:
        return
    try:
        from src.core.redis import get_redis_client

        r = get_redis_client()
        if r is not None:
            r.set("authentik:sid:" + flow_store._hash(sid), str(user_id), ex=SID_TTL_SECONDS)
    except Exception:
        logger.warning("Could not record Authentik session id for back-channel logout")


async def _validated_claims(settings: AuthentikSettings, metadata: oidc.ProviderMetadata, body: CallbackBody, record: flow_store.FlowState) -> dict:
    tokens = await oidc.exchange_code(
        settings=settings, metadata=metadata, code=body.code, code_verifier=record.code_verifier
    )
    claims = await oidc.validate_id_token(
        tokens["id_token"],
        settings=settings,
        metadata=metadata,
        nonce=record.nonce,
        access_token=tokens["access_token"],
    )
    userinfo = await oidc.fetch_userinfo(
        settings=settings, metadata=metadata, access_token=tokens["access_token"], expected_sub=claims["sub"]
    )
    # Signed ID-token claims win over userinfo for anything both carry.
    return {**userinfo, **claims}


@router.post("/callback", summary="Finish an Authentik sign-in")
async def authentik_callback(
    request: Request,
    response: Response,
    body: CallbackBody,
    db_session: AsyncSession = Depends(get_db_session),
):
    settings = _settings_or_404()
    _rate_limit(request)

    record = flow_store.consume_flow(body.state, body.binding)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This sign-in link has expired or was already used. Please start again.",
        )

    metadata = await _metadata(settings)
    # RFC 9207: when the IdP names itself in the redirect, it must be ours.
    if body.iss is not None and body.iss.rstrip("/") != metadata.issuer.rstrip("/"):
        logger.warning("Authentik callback carried a foreign iss parameter")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_FAILURE)

    try:
        claims = await _validated_claims(settings, metadata, body, record)
    except oidc.OIDCError as exc:
        logger.warning("Authentik sign-in rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=GENERIC_FAILURE) from exc

    issuer = metadata.issuer
    subject: str = claims["sub"]
    groups = identity.claim_groups(claims)
    identity.check_allowed_groups(settings, groups)
    email = oidc.verified_email(claims)

    org = await _org_or_400(db_session, record.org_id)
    if org is not None:
        from src.services.orgs.auth_policy import enforce_login_auth_method

        await enforce_login_auth_method(db_session, org.id, AUTH_METHOD_AUTHENTIK)

    user = await identity.find_linked_user(db_session, issuer, subject)
    found_by_link = user is not None
    newly_linked = False
    if user is None:
        if email is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentik did not return a verified email address for this account.",
            )
        user = await identity.find_user_by_email(db_session, email)
        if user is not None:
            if not settings.link_existing_by_email:
                raise identity._forbidden(
                    "An account with this email already exists. Sign in with it first to link Authentik."
                )
            if await identity._existing_link_for_user(db_session, user.id, issuer) is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This account is already linked to a different Authentik identity.",
                )

    admission = identity.OrgAdmission()
    if org is not None:
        admission = await identity.admit_to_org(
            request, db_session, settings, org, email or user.email.lower(), user, record.invite_code
        )

    if user is None:
        if not settings.auto_provision:
            raise identity._forbidden("No LearnHouse account exists for this Authentik user.")
        assert email is not None
        user = await identity.provision_user(request, db_session, claims, email, org.id if org else None)
        await identity.link_identity(db_session, user, issuer, subject, email)
    else:
        if not found_by_link:
            assert email is not None
            await identity.apply_prehijack_guard(db_session, user)
            await identity.link_identity(db_session, user, issuer, subject, email)
            newly_linked = True
        else:
            await identity.touch_identity(db_session, issuer, subject)
        if org is not None:
            await identity.join_org(request, db_session, user, org.id)

    if org is not None:
        await identity.finish_admission(request, db_session, user, admission)
        await identity.sync_org_role(db_session, settings, user, org.id, groups)

    _remember_sid(claims.get("sid"), user.id)

    from src.db.user_audit_events import UserAuditEventType
    from src.services.audit.audit import record_audit_event
    from src.services.security.account_lockout import update_login_info

    client_ip = get_client_ip(request)
    await update_login_info(user, client_ip, db_session)
    await record_audit_event(
        event_type=UserAuditEventType.LOGIN,
        user_id=user.id,
        org_id=org.id if org else None,
        ip=client_ip,
        user_agent=request.headers.get("user-agent"),
        metadata={"method": AUTH_METHOD_AUTHENTIK, "linked": newly_linked},
    )

    from src.routers.auth import _bind_session_to_custom_domain, get_token_expiry_ms, set_auth_cookies
    from src.services.auth.session import issue_session_or_challenge

    session_org_id, on_custom_domain = await _bind_session_to_custom_domain(
        request, db_session, user.id, org.id if org else None, AUTH_METHOD_AUTHENTIK
    )
    issue = await issue_session_or_challenge(
        db_session, user, amr=AUTH_METHOD_AUTHENTIK, org_id=session_org_id, custom_domain=on_custom_domain
    )
    if issue.mfa_required:
        return {"mfa_required": True, "mfa_token": issue.mfa_token, "callback_url": record.next_path}

    set_auth_cookies(response, issue.access_token, issue.refresh_token, request)
    return {
        "user": UserRead.model_validate(user),
        "tokens": {
            "access_token": issue.access_token,
            "refresh_token": issue.refresh_token,
            "expiry": get_token_expiry_ms(),
        },
        "callback_url": record.next_path,
    }


@router.get("/logout-url", summary="Where to send the browser to end the Authentik session")
async def authentik_logout_url():
    settings = get_authentik_settings()
    if not settings.enabled:
        return {"url": None}
    try:
        metadata = await oidc.get_metadata(settings)
    except oidc.OIDCError:
        return {"url": None}
    if not metadata.end_session_endpoint:
        return {"url": None}
    params = {"client_id": settings.client_id}
    if settings.post_logout_redirect_uri:
        params["post_logout_redirect_uri"] = settings.post_logout_redirect_uri
    sep = "&" if "?" in metadata.end_session_endpoint else "?"
    return {"url": metadata.end_session_endpoint + sep + urlencode(params)}


@router.post("/backchannel-logout", summary="OpenID Connect back-channel logout receiver")
async def authentik_backchannel_logout(
    response: Response,
    logout_token: str = Form(..., max_length=16384),
    db_session: AsyncSession = Depends(get_db_session),
):
    response.headers["Cache-Control"] = "no-store"
    settings = get_authentik_settings()
    if not settings.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not enabled")
    metadata = await _metadata(settings)
    try:
        claims = await oidc.validate_logout_token(logout_token, settings=settings, metadata=metadata)
    except oidc.OIDCError as exc:
        logger.warning("Authentik back-channel logout rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from exc

    if not flow_store.remember_logout_jti(claims["jti"], oidc.MAX_ID_TOKEN_AGE_SECONDS * 2):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request")

    user_ids: set[int] = set()
    sub = claims.get("sub")
    if isinstance(sub, str):
        user = await identity.find_linked_user(db_session, metadata.issuer, sub)
        if user is not None and user.id is not None:
            user_ids.add(user.id)
    sid = claims.get("sid")
    if isinstance(sid, str):
        from src.core.redis import get_redis_client

        r = get_redis_client()
        raw = r.get("authentik:sid:" + flow_store._hash(sid)) if r is not None else None
        if raw:
            user_ids.add(int(raw))

    from src.security.auth import revoke_user_sessions_before

    for user_id in user_ids:
        if not revoke_user_sessions_before(user_id):
            logger.error("Authentik back-channel logout could not revoke sessions of user %s", user_id)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="temporarily_unavailable")
        logger.info("Authentik back-channel logout ended the sessions of user %s", user_id)
    return {"ok": True}
