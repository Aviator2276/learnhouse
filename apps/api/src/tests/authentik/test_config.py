import pytest

from src.services.auth.authentik import config as cfg
from src.services.auth.authentik.config import (
    AuthentikConfigError,
    get_authentik_settings,
    parse_role_mapping,
    reset_authentik_settings_cache,
)


BASE_ENV = {
    "LEARNHOUSE_AUTHENTIK_ENABLED": "true",
    "LEARNHOUSE_AUTHENTIK_ISSUER": "https://auth.example.com/application/o/lh/",
    "LEARNHOUSE_AUTHENTIK_CLIENT_ID": "cid",
    "LEARNHOUSE_AUTHENTIK_CLIENT_SECRET": "secret",
    "LEARNHOUSE_AUTHENTIK_REDIRECT_URI": "https://lh.example.com/auth/callback/authentik",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(cfg.ENV_PREFIX):
            monkeypatch.delenv(key)
    reset_authentik_settings_cache()
    yield
    reset_authentik_settings_cache()


def _apply(monkeypatch, **extra):
    env = {**BASE_ENV, **extra}
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_disabled_by_default():
    assert get_authentik_settings().enabled is False


def test_full_config_loads(monkeypatch):
    _apply(
        monkeypatch,
        LEARNHOUSE_AUTHENTIK_SCOPES="email profile groups",
        LEARNHOUSE_AUTHENTIK_ALLOWED_GROUPS="a, b",
        LEARNHOUSE_AUTHENTIK_ROLE_MAPPING="lh-admins=admin,lh staff=role_global_instructor",
        LEARNHOUSE_AUTHENTIK_DISPLAY_NAME="Company SSO",
        LEARNHOUSE_AUTHENTIK_HTTP_TIMEOUT="500",
        LEARNHOUSE_AUTHENTIK_POST_LOGOUT_REDIRECT_URI="https://lh.example.com/bye",
    )
    s = get_authentik_settings()
    assert s.enabled
    assert s.scopes == ("openid", "email", "profile", "groups")
    assert s.allowed_groups == frozenset({"a", "b"})
    assert s.role_mapping == {"lh-admins": "role_global_admin", "lh staff": "role_global_instructor"}
    assert s.display_name == "Company SSO"
    assert s.http_timeout == 60.0
    assert s.post_logout_redirect_uri == "https://lh.example.com/bye"
    assert s.issuer_normalized == "https://auth.example.com/application/o/lh"
    assert "secret" not in repr(s)


def test_secret_from_file(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret"
    secret_file.write_text("from-file\n")
    _apply(
        monkeypatch,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET=None,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET_FILE=str(secret_file),
    )
    assert get_authentik_settings().client_secret == "from-file"


def test_unreadable_secret_file_disables(monkeypatch, tmp_path):
    _apply(
        monkeypatch,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET=None,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET_FILE=str(tmp_path / "missing"),
    )
    assert get_authentik_settings().enabled is False


def test_empty_secret_file_disables(monkeypatch, tmp_path):
    secret_file = tmp_path / "secret"
    secret_file.write_text("   ")
    _apply(
        monkeypatch,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET=None,
        LEARNHOUSE_AUTHENTIK_CLIENT_SECRET_FILE=str(secret_file),
    )
    assert get_authentik_settings().enabled is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"LEARNHOUSE_AUTHENTIK_CLIENT_ID": None},
        {"LEARNHOUSE_AUTHENTIK_ISSUER": "http://auth.example.com/"},
        {"LEARNHOUSE_AUTHENTIK_ISSUER": "ftp://auth.example.com/"},
        {"LEARNHOUSE_AUTHENTIK_ISSUER": "https://user:pw@auth.example.com/"},
        {"LEARNHOUSE_AUTHENTIK_ISSUER": "https://auth.example.com/#frag"},
        {"LEARNHOUSE_AUTHENTIK_REDIRECT_URI": "https://lh.example.com/cb?x=1"},
        {"LEARNHOUSE_AUTHENTIK_POST_LOGOUT_REDIRECT_URI": "javascript:alert(1)"},
        {"LEARNHOUSE_AUTHENTIK_HTTP_TIMEOUT": "soon"},
        {"LEARNHOUSE_AUTHENTIK_CA_BUNDLE": "/nonexistent/ca.pem"},
        {"LEARNHOUSE_AUTHENTIK_ROLE_MAPPING": "admins=superuser"},
        {"LEARNHOUSE_AUTHENTIK_ROLE_MAPPING": "no-equals-sign"},
    ],
)
def test_invalid_config_fails_closed(monkeypatch, overrides):
    _apply(monkeypatch, **overrides)
    assert get_authentik_settings().enabled is False


def test_http_allowed_only_when_opted_in(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    _apply(
        monkeypatch,
        LEARNHOUSE_AUTHENTIK_ISSUER="http://authentik.lan:9000/application/o/lh/",
        LEARNHOUSE_AUTHENTIK_ALLOW_INSECURE_HTTP="yes",
        LEARNHOUSE_AUTHENTIK_CA_BUNDLE=str(ca),
    )
    s = get_authentik_settings()
    assert s.enabled and s.allow_insecure_http and s.ca_bundle == str(ca)


def test_redirect_defaults_from_learnhouse_domain(monkeypatch):
    _apply(monkeypatch, LEARNHOUSE_AUTHENTIK_REDIRECT_URI=None)
    monkeypatch.setattr(cfg, "_default_base_url", lambda: "https://learn.example.com")
    s = get_authentik_settings()
    assert s.redirect_uri == "https://learn.example.com/auth/callback/authentik"
    assert s.post_logout_redirect_uri == "https://learn.example.com/"


def test_redirect_required_without_domain(monkeypatch):
    _apply(monkeypatch, LEARNHOUSE_AUTHENTIK_REDIRECT_URI=None)
    monkeypatch.setattr(cfg, "_default_base_url", lambda: None)
    assert get_authentik_settings().enabled is False


def test_default_base_url_uses_config(monkeypatch):
    from types import SimpleNamespace

    # frontend_domain defaults to localhost:3000 in LearnHouse; it must be ignored.
    fake = SimpleNamespace(hosting_config=SimpleNamespace(frontend_domain="localhost:3000", domain="lh.local:8080", ssl=False))
    monkeypatch.setattr("config.config.get_learnhouse_config", lambda: fake)
    assert cfg._default_base_url() == "http://lh.local:8080"
    fake.hosting_config.domain = ""
    assert cfg._default_base_url() is None


def test_parse_role_mapping_empty():
    assert parse_role_mapping(None) == {}
    assert parse_role_mapping(" , ") == {}


def test_parse_role_mapping_rejects_blank_group():
    with pytest.raises(AuthentikConfigError):
        parse_role_mapping("=admin")
