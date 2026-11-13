"""End-to-end tests of the Authentik sign-in endpoints against the fake IdP.

Each test drives the real handlers: authorize -> (fake Authentik) -> callback,
with a real SQLite database, fakeredis and real signed tokens.
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from sqlmodel import select

from src.db.organization_config import OrganizationConfig
from src.db.roles import Role, RoleTypeEnum
from src.db.user_identities import UserExternalIdentity
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.routers import authentik as router
from src.services.auth.authentik import flow as flow_store
from src.services.auth.authentik import identity, oidc

from .conftest import ISSUER, make_request, make_settings


@pytest.fixture
def current(monkeypatch, settings):
    holder = SimpleNamespace(settings=settings)
    monkeypatch.setattr(router, "get_authentik_settings", lambda: holder.settings)
    monkeypatch.setattr(router, "check_login_rate_limit", lambda request: (True, 0))
    return holder


@pytest.fixture
def env(idp, redis_client, current):
    return SimpleNamespace(idp=idp, redis=redis_client, current=current)


async def _set_signup_mode(db, org_id: int, mode: str, **security):
    config = {"config_version": "1.0", "features": {"members": {"signup_mode": mode}}}
    if security:
        config["admin_toggles"] = {"security": security}
    db.add(OrganizationConfig(org_id=org_id, config=config))
    await db.commit()


async def _global_roles(db):
    roles = {}
    for rid, uuid in ((11, "role_global_admin"), (12, "role_global_maintainer"), (13, "role_global_instructor"), (14, "role_global_user")):
        role = Role(id=rid, name=uuid, role_type=RoleTypeEnum.TYPE_GLOBAL, role_uuid=uuid, rights={},
                    creation_date=str(datetime.now()), update_date=str(datetime.now()))
        db.add(role)
        roles[uuid] = role
    await db.commit()
    return roles


async def _authorize(db, env, **body):
    result = await router.authentik_authorize(make_request(), router.AuthorizeBody(**body), db)
    env.idp.capture_nonce(result["url"])
    return result


async def _login(db, env, *, iss=None, binding=None, **authorize):
    started = await _authorize(db, env, **authorize)
    response = Response()
    result = await router.authentik_callback(
        make_request(),
        response,
        router.CallbackBody(code="auth-code", state=started["state"], binding=binding or started["binding"], iss=iss),
        db,
    )
    return result, response


async def _links(db):
    return (await db.execute(select(UserExternalIdentity))).scalars().all()


# -- /config ------------------------------------------------------------------


async def test_config_endpoint(current):
    assert await router.authentik_config() == {"enabled": True, "display_name": "Authentik"}
    current.settings = make_settings(enabled=False)
    assert await router.authentik_config() == {"enabled": False}


# -- /authorize ---------------------------------------------------------------


async def test_authorize_builds_pkce_request(db, env):
    result = await _authorize(db, env, next="/courses")
    params = env.idp.capture_nonce(result["url"])
    assert result["url"].startswith(env.idp.discovery["authorization_endpoint"] + "?")
    assert params["code_challenge_method"] == "S256"
    assert params["client_id"] == "lh-client"
    assert params["redirect_uri"] == "https://lh.test/auth/callback/authentik"
    assert params["scope"] == "openid email profile"
    assert params["state"] == result["state"] and params["nonce"]
    assert "lh-client-secret" not in result["url"]


async def test_authorize_appends_to_existing_query(db, env):
    env.idp.discovery["authorization_endpoint"] = "https://auth.test/authorize/?tenant=x"
    result = await _authorize(db, env)
    assert "?tenant=x&response_type=code" in result["url"]


async def test_authorize_disabled_is_404(db, env):
    env.current.settings = make_settings(enabled=False)
    with pytest.raises(HTTPException) as exc:
        await router.authentik_authorize(make_request(), router.AuthorizeBody(), db)
    assert exc.value.status_code == 404


async def test_authorize_rate_limited(db, env, monkeypatch):
    monkeypatch.setattr(router, "check_login_rate_limit", lambda request: (False, 120))
    with pytest.raises(HTTPException) as exc:
        await router.authentik_authorize(make_request(), router.AuthorizeBody(), db)
    assert exc.value.status_code == 429


async def test_authorize_unknown_org(db, env):
    with pytest.raises(HTTPException) as exc:
        await router.authentik_authorize(make_request(), router.AuthorizeBody(org_id=999), db)
    assert exc.value.status_code == 400


async def test_authorize_refused_when_org_disallows_method(db, env, org):
    await _set_signup_mode(db, org.id, "open", allowed_auth_methods=["password"])
    with pytest.raises(HTTPException) as exc:
        await router.authentik_authorize(make_request(), router.AuthorizeBody(org_id=org.id), db)
    assert exc.value.status_code == 403


async def test_authorize_discovery_down_is_503(db, env):
    env.idp.discovery_status = 502
    with pytest.raises(HTTPException) as exc:
        await router.authentik_authorize(make_request(), router.AuthorizeBody(), db)
    assert exc.value.status_code == 503


# -- /callback: happy paths -------------------------------------------------


async def test_first_login_provisions_and_links(db, env):
    result, response = await _login(db, env, next="/after")
    assert result["callback_url"] == "/after"
    assert result["tokens"]["access_token"]
    assert "set-cookie" in response.headers
    user = (await db.execute(select(User))).scalars().one()
    assert user.email == "alice@example.com" and user.signup_method == "authentik"
    assert user.first_name == "Alice" and user.username.startswith("alice")
    links = await _links(db)
    assert [(link.issuer, link.subject, link.user_id) for link in links] == [(ISSUER.rstrip("/") + "/", "ak-sub-123", user.id)]
    # Nothing from Authentik is handed back to the browser.
    body = json.dumps(result, default=str)
    assert "ak-access-token" not in body and env.idp.nonce not in body


async def test_repeat_login_uses_sub_not_email(db, env):
    await _login(db, env)
    # Same Authentik account, email changed at the IdP: still the same user.
    env.idp.claims["email"] = "renamed@example.com"
    await _login(db, env)
    users = (await db.execute(select(User))).scalars().all()
    assert len(users) == 1 and users[0].email == "alice@example.com"
    assert (await _links(db))[0].last_login_at


async def test_repeat_login_without_email_claim(db, env):
    await _login(db, env)
    env.idp.claims.pop("email")
    result, _ = await _login(db, env)
    assert result["user"].email == "alice@example.com"


async def test_open_org_join_on_first_login(db, env, org):
    await _set_signup_mode(db, org.id, "open")
    await _login(db, env, org_id=org.id)
    membership = (await db.execute(select(UserOrganization))).scalars().one()
    assert membership.org_id == org.id


async def test_existing_user_joins_open_org(db, env, org, regular_user):
    await _set_signup_mode(db, org.id, "open")
    env.idp.claims["email"] = regular_user.email.upper()  # fixture user is already a member
    await _login(db, env, org_id=org.id)
    links = await _links(db)
    assert links[0].user_id == regular_user.id


async def test_existing_non_member_joins_open_org(db, env, org):
    await _set_signup_mode(db, org.id, "open")
    await _login(db, env)  # account exists, no org yet
    await _login(db, env, org_id=org.id)
    memberships = (await db.execute(select(UserOrganization))).scalars().all()
    assert [m.org_id for m in memberships] == [org.id]


async def test_mfa_challenge_is_respected(db, env, monkeypatch):
    monkeypatch.setattr("src.services.auth.session.is_mfa_active", AsyncMock(return_value=True))
    result, response = await _login(db, env, next="/x")
    assert result["mfa_required"] is True and result["mfa_token"]
    assert result["callback_url"] == "/x"
    assert "set-cookie" not in response.headers


async def test_sid_is_remembered_for_backchannel_logout(db, env):
    env.idp.claims["sid"] = "authentik-session-1"
    await _login(db, env)
    assert env.redis.get("authentik:sid:" + flow_store._hash("authentik-session-1"))


# -- /callback: refusals ---------------------------------------------------


async def test_callback_disabled(db, env):
    env.current.settings = make_settings(enabled=False)
    with pytest.raises(HTTPException) as exc:
        await router.authentik_callback(make_request(), Response(), router.CallbackBody(code="c", state="s", binding="b"), db)
    assert exc.value.status_code == 404


async def test_callback_unknown_state(db, env):
    with pytest.raises(HTTPException) as exc:
        await router.authentik_callback(
            make_request(), Response(), router.CallbackBody(code="c", state="a" * 43, binding="b"), db
        )
    assert exc.value.status_code == 400


async def test_callback_wrong_binding_is_login_csrf(db, env):
    with pytest.raises(HTTPException) as exc:
        await _login(db, env, binding="someone-elses-binding")
    assert exc.value.status_code == 400
    assert env.idp.token_requests == []


async def test_callback_foreign_iss_parameter(db, env):
    with pytest.raises(HTTPException) as exc:
        await _login(db, env, iss="https://evil.test/")
    assert exc.value.status_code == 401


async def test_callback_matching_iss_parameter(db, env):
    result, _ = await _login(db, env, iss=ISSUER)
    assert result["tokens"]


async def test_callback_invalid_id_token(db, env):
    env.idp.id_token_override = lambda token: env.idp.sign(env.idp.base_claims(nonce="replayed"))
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 401
    assert (await db.execute(select(User))).scalars().first() is None


async def test_callback_group_not_allowed(db, env):
    env.current.settings = make_settings(allowed_groups=frozenset({"lh-users"}))
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 403
    env.idp.claims["groups"] = ["lh-users", 7]
    result, _ = await _login(db, env)
    assert result["tokens"]


async def test_callback_unverified_email_refused(db, env):
    env.idp.claims["email_verified"] = False
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 401


async def test_callback_org_method_disallowed(db, env, org):
    started = await _authorize(db, env, org_id=org.id)
    await _set_signup_mode(db, org.id, "open", allowed_auth_methods=["password"])
    with pytest.raises(HTTPException) as exc:
        await router.authentik_callback(
            make_request(), Response(),
            router.CallbackBody(code="c", state=started["state"], binding=started["binding"]), db,
        )
    assert exc.value.status_code == 403


async def test_callback_discovery_down(db, env):
    started = await _authorize(db, env)
    oidc.clear_caches()
    env.idp.discovery_status = 500
    with pytest.raises(HTTPException) as exc:
        await router.authentik_callback(
            make_request(), Response(),
            router.CallbackBody(code="c", state=started["state"], binding=started["binding"]), db,
        )
    assert exc.value.status_code == 503


# -- account linking ----------------------------------------------------------


async def test_links_existing_verified_account(db, env, regular_user):
    env.idp.claims["email"] = regular_user.email
    result, _ = await _login(db, env)
    assert result["user"].id == regular_user.id
    assert (await _links(db))[0].user_id == regular_user.id


async def test_prehijack_guard_drops_untrusted_password(db, env, regular_user, monkeypatch):
    regular_user = await db.get(User, regular_user.id)
    regular_user.email_verified = False
    regular_user.password = "argon-hash"
    regular_user.signup_method = None
    db.add(regular_user)
    await db.commit()
    revoked = []
    monkeypatch.setattr("src.security.auth.revoke_user_sessions_before", lambda uid: revoked.append(uid) or True)
    env.idp.claims["email"] = regular_user.email
    await _login(db, env)
    await db.refresh(regular_user)
    assert regular_user.password == "" and regular_user.email_verified is True
    assert regular_user.signup_method == "authentik"
    assert revoked == [regular_user.id]


async def test_linking_by_email_can_be_disabled(db, env, regular_user):
    env.current.settings = make_settings(link_existing_by_email=False)
    env.idp.claims["email"] = regular_user.email
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 403


async def test_account_linked_to_other_subject_is_not_taken_over(db, env, regular_user):
    db.add(UserExternalIdentity(user_id=regular_user.id, provider="authentik", issuer=ISSUER, subject="original-sub"))
    await db.commit()
    env.idp.claims["email"] = regular_user.email
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 409


async def test_auto_provision_off(db, env):
    env.current.settings = make_settings(auto_provision=False)
    with pytest.raises(HTTPException) as exc:
        await _login(db, env)
    assert exc.value.status_code == 403


async def test_link_race_is_a_conflict(db, env, regular_user):
    db.add(UserExternalIdentity(user_id=regular_user.id, provider="authentik", issuer=ISSUER, subject="s1"))
    await db.commit()
    with pytest.raises(HTTPException) as exc:
        await identity.link_identity(db, regular_user, ISSUER, "s2", regular_user.email)
    assert exc.value.status_code == 409


# -- invite-only orgs -------------------------------------------------------


async def test_invite_only_without_invite(db, env, org):
    await _set_signup_mode(db, org.id, "inviteOnly")
    with pytest.raises(HTTPException) as exc:
        await _login(db, env, org_id=org.id)
    assert exc.value.status_code == 403
    assert (await db.execute(select(User))).scalars().first() is None


async def test_invite_only_with_emailed_invite(db, env, org):
    await _set_signup_mode(db, org.id, "inviteOnly")
    key = f"invited_user:alice@example.com:org:{org.org_uuid}"
    env.redis.set(key, json.dumps({"pending": True}), ex=3600)
    await _login(db, env, org_id=org.id)
    assert json.loads(env.redis.get(key))["pending"] is False
    assert (await db.execute(select(UserOrganization))).scalars().one().org_id == org.id


async def test_invite_only_with_invite_code(db, env, org, monkeypatch):
    await _set_signup_mode(db, org.id, "inviteOnly")
    monkeypatch.setattr("src.services.orgs.invites.get_invite_code", AsyncMock(return_value={"usergroup_id": 5}))
    attach = AsyncMock()
    monkeypatch.setattr("src.services.users.usergroups.add_users_to_usergroup", attach)
    await _login(db, env, org_id=org.id, invite_code="CODE")
    assert attach.await_args.args[3] == 5


async def test_invite_code_invalid_falls_back_to_email_invite(db, env, org, monkeypatch):
    await _set_signup_mode(db, org.id, "inviteOnly")
    monkeypatch.setattr(
        "src.services.orgs.invites.get_invite_code", AsyncMock(side_effect=HTTPException(status_code=404))
    )
    with pytest.raises(HTTPException) as exc:
        await _login(db, env, org_id=org.id, invite_code="BAD")
    assert exc.value.status_code == 403


async def test_invite_only_existing_member_signs_in(db, env, org, regular_user):
    await _set_signup_mode(db, org.id, "inviteOnly")
    env.idp.claims["email"] = regular_user.email  # fixture user is already a member
    result, _ = await _login(db, env, org_id=org.id)
    assert result["user"].id == regular_user.id


async def test_invite_bypass(db, env, org):
    await _set_signup_mode(db, org.id, "inviteOnly")
    env.current.settings = make_settings(bypass_invite=True)
    await _login(db, env, org_id=org.id)
    assert (await db.execute(select(UserOrganization))).scalars().one().org_id == org.id


async def test_invite_check_redis_failures(db, env, org, monkeypatch):
    await _set_signup_mode(db, org.id, "inviteOnly")
    request = make_request()
    settings = env.current.settings

    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    with pytest.raises(HTTPException) as exc:
        await identity.admit_to_org(request, db, settings, org, "a@b.c", None, None)
    assert exc.value.status_code == 503

    broken = SimpleNamespace(get=lambda key: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: broken)
    with pytest.raises(HTTPException) as exc:
        await identity.admit_to_org(request, db, settings, org, "a@b.c", None, None)
    assert exc.value.status_code == 503


async def test_finish_admission_is_best_effort(db, env, regular_user, monkeypatch):
    monkeypatch.setattr(
        "src.services.users.usergroups.add_users_to_usergroup", AsyncMock(side_effect=RuntimeError())
    )
    broken = SimpleNamespace(get=lambda key: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: broken)
    await identity.finish_admission(
        make_request(), db, regular_user, identity.OrgAdmission(usergroup_id=1, consume_invite_key="k")
    )
    # Invite key gone (expired) between admission and finish: nothing to mark.
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: env.redis)
    await identity.finish_admission(make_request(), db, regular_user, identity.OrgAdmission(consume_invite_key="gone"))
    env.redis.set("persist", json.dumps({"pending": True}))
    await identity.finish_admission(make_request(), db, regular_user, identity.OrgAdmission(consume_invite_key="persist"))
    assert json.loads(env.redis.get("persist"))["pending"] is False


# -- group -> role sync ---------------------------------------------------


@pytest.fixture
async def roles(db):
    return await _global_roles(db)


async def _membership(db, user_id, org_id):
    return (await db.execute(select(UserOrganization).where(
        (UserOrganization.user_id == user_id) & (UserOrganization.org_id == org_id)))).scalars().one()


async def test_role_sync_promotes_and_demotes(db, env, org, roles):
    await _set_signup_mode(db, org.id, "open")
    env.current.settings = make_settings(role_mapping={"lh-admins": "role_global_admin", "lh-teach": "role_global_instructor"})
    env.idp.claims["groups"] = ["lh-teach", "lh-admins"]
    await _login(db, env, org_id=org.id)
    user = (await db.execute(select(User))).scalars().one()
    assert (await _membership(db, user.id, org.id)).role_id == roles["role_global_admin"].id

    # Another admin exists, so removing alice from lh-admins demotes her.
    db.add(UserOrganization(user_id=999, org_id=org.id, role_id=roles["role_global_admin"].id,
                            creation_date="", update_date=""))
    await db.commit()
    env.idp.claims["groups"] = ["lh-teach"]
    await _login(db, env, org_id=org.id)
    assert (await _membership(db, user.id, org.id)).role_id == roles["role_global_instructor"].id

    env.idp.claims["groups"] = []
    await _login(db, env, org_id=org.id)
    assert (await _membership(db, user.id, org.id)).role_id == roles["role_global_user"].id


async def test_role_sync_never_demotes_last_admin(db, env, org, roles):
    await _set_signup_mode(db, org.id, "open")
    env.current.settings = make_settings(role_mapping={"lh-admins": "role_global_admin"})
    env.idp.claims["groups"] = ["lh-admins"]
    await _login(db, env, org_id=org.id)
    env.idp.claims["groups"] = []
    await _login(db, env, org_id=org.id)
    user = (await db.execute(select(User))).scalars().one()
    assert (await _membership(db, user.id, org.id)).role_id == roles["role_global_admin"].id


async def test_role_sync_off_or_unmapped(db, env, org, roles, regular_user):
    s = make_settings(role_mapping={"g": "role_global_admin"}, role_sync=False)
    assert identity.desired_role_uuid(s, ["g"]) is None
    assert identity.desired_role_uuid(make_settings(), ["g"]) is None
    # No membership in the org: nothing to sync.
    await identity.sync_org_role(db, make_settings(role_mapping={"g": "role_global_admin"}), regular_user, 999, frozenset({"g"}))


async def test_role_sync_missing_role_row(db, env, org, regular_user):
    await identity.sync_org_role(db, make_settings(role_mapping={"g": "role_global_admin"}), regular_user, org.id, frozenset({"g"}))
    assert (await _membership(db, regular_user.id, org.id)).role_id == 4


async def test_role_sync_demotion_without_admin_role_row(db, env, org, regular_user):
    # Only the target role exists; the "is this the last admin" check is skipped.
    db.add(Role(id=14, name="u", role_type=RoleTypeEnum.TYPE_GLOBAL, role_uuid="role_global_user", rights={},
                creation_date="", update_date=""))
    membership = await _membership(db, regular_user.id, org.id)
    membership.role_id = 12
    db.add(membership)
    await db.commit()
    await identity.sync_org_role(db, make_settings(role_mapping={"g": "role_global_admin"}), regular_user, org.id, frozenset())
    assert (await _membership(db, regular_user.id, org.id)).role_id == 14


# -- identity helpers -----------------------------------------------------------


def test_claim_groups_shapes():
    assert identity.claim_groups({"groups": "admins"}) == frozenset()
    assert identity.claim_groups({"groups": ["a", "", None, "x" * 300]}) == frozenset({"a"})


def test_username_and_names_from_claims():
    assert identity._username_from_claims({"preferred_username": "!!!"}, "x@y.z").startswith("user")
    assert identity._username_from_claims({}, "bob.smith@y.z").startswith("bob.smith")
    assert identity._name_part({"given_name": 5}, "given_name") == ""


async def test_provision_splits_full_name(db, env):
    env.idp.claims = {"email": "carol@example.com", "email_verified": True, "name": "Carol Ann Smith"}
    result, _ = await _login(db, env)
    assert (result["user"].first_name, result["user"].last_name) == ("Carol", "Ann Smith")


async def test_touch_identity_without_link_is_noop(db):
    await identity.touch_identity(db, ISSUER, "nobody")


async def test_find_linked_user_dangling(db):
    db.add(UserExternalIdentity(user_id=4242, provider="authentik", issuer=ISSUER, subject="ghost"))
    await db.commit()
    assert await identity.find_linked_user(db, ISSUER, "ghost") is None


def test_remember_sid_guards(env, monkeypatch):
    router._remember_sid(None, 1)
    router._remember_sid("x" * 300, 1)
    broken = SimpleNamespace(set=lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: broken)
    router._remember_sid("sid", 1)  # logged, not raised
    monkeypatch.setattr("src.core.redis.get_redis_client", lambda: None)
    router._remember_sid("sid", 1)


# -- /logout-url ---------------------------------------------------------------


async def test_logout_url(env):
    url = (await router.authentik_logout_url())["url"]
    assert url.startswith(env.idp.discovery["end_session_endpoint"] + "?client_id=lh-client")
    assert "post_logout_redirect_uri=https%3A%2F%2Flh.test%2F" in url


async def test_logout_url_variants(env):
    env.current.settings = make_settings(post_logout_redirect_uri="")
    oidc.clear_caches()
    env.idp.discovery["end_session_endpoint"] = "https://auth.test/end/?x=1"
    assert (await router.authentik_logout_url())["url"] == "https://auth.test/end/?x=1&client_id=lh-client"

    oidc.clear_caches()
    env.idp.discovery.pop("end_session_endpoint")
    assert (await router.authentik_logout_url())["url"] is None

    oidc.clear_caches()
    env.idp.discovery_status = 500
    assert (await router.authentik_logout_url())["url"] is None

    env.current.settings = make_settings(enabled=False)
    assert (await router.authentik_logout_url())["url"] is None


# -- /backchannel-logout --------------------------------------------------------


async def test_backchannel_logout_by_sub_and_sid(db, env, monkeypatch):
    env.idp.claims["sid"] = "sess-9"
    await _login(db, env)
    user = (await db.execute(select(User))).scalars().one()
    revoked = []
    monkeypatch.setattr("src.security.auth.revoke_user_sessions_before", lambda uid: revoked.append(uid) or True)

    response = Response()
    result = await router.authentik_backchannel_logout(response, env.idp.logout_token(), db)
    assert result == {"ok": True} and revoked == [user.id]
    assert response.headers["cache-control"] == "no-store"

    await router.authentik_backchannel_logout(Response(), env.idp.logout_token(sub=None, sid="sess-9", jti="j2"), db)
    assert revoked == [user.id, user.id]

    # Unknown subject: accepted, nothing to revoke.
    await router.authentik_backchannel_logout(Response(), env.idp.logout_token(sub="unknown", jti="j3"), db)
    assert len(revoked) == 2


async def test_backchannel_logout_replay_and_invalid(db, env, monkeypatch):
    monkeypatch.setattr("src.security.auth.revoke_user_sessions_before", lambda uid: True)
    token = env.idp.logout_token(jti="same")
    await router.authentik_backchannel_logout(Response(), token, db)
    with pytest.raises(HTTPException) as exc:
        await router.authentik_backchannel_logout(Response(), token, db)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await router.authentik_backchannel_logout(Response(), env.idp.logout_token(nonce="n", jti="j9"), db)
    assert exc.value.status_code == 400


async def test_backchannel_logout_revocation_failure(db, env, monkeypatch):
    await _login(db, env)
    monkeypatch.setattr("src.security.auth.revoke_user_sessions_before", lambda uid: False)
    with pytest.raises(HTTPException) as exc:
        await router.authentik_backchannel_logout(Response(), env.idp.logout_token(jti="jf"), db)
    assert exc.value.status_code == 503


async def test_backchannel_logout_sid_without_redis(db, env, monkeypatch):
    token = env.idp.logout_token(sub=None, sid="s", jti="jr")
    real = env.redis
    calls = {"n": 0}

    def client():
        calls["n"] += 1
        return real if calls["n"] == 1 else None  # jti check works, sid lookup has no Redis

    monkeypatch.setattr("src.core.redis.get_redis_client", client)
    assert await router.authentik_backchannel_logout(Response(), token, db) == {"ok": True}


async def test_backchannel_logout_disabled(db, env):
    env.current.settings = make_settings(enabled=False)
    with pytest.raises(HTTPException) as exc:
        await router.authentik_backchannel_logout(Response(), "x", db)
    assert exc.value.status_code == 404

