import pytest

from src.services.plugnmeet import config as cfg
from src.services.plugnmeet.config import get_plugnmeet_settings, is_private_host, reset_plugnmeet_settings_cache

BASE_ENV = {
    "LEARNHOUSE_PLUGNMEET_ENABLED": "true",
    "LEARNHOUSE_PLUGNMEET_SERVER_URL": "http://192.168.1.20:8080/",
    "LEARNHOUSE_PLUGNMEET_CLIENT_URL": "https://meet.example.com",
    "LEARNHOUSE_PLUGNMEET_API_KEY": "plugnmeet",
    "LEARNHOUSE_PLUGNMEET_API_SECRET": "0123456789abcdef0123",
    "LEARNHOUSE_PLUGNMEET_PUBLIC_URL": "https://learn.example.com/",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os

    for key in list(os.environ):
        if key.startswith(cfg.ENV_PREFIX):
            monkeypatch.delenv(key)
    reset_plugnmeet_settings_cache()
    yield
    reset_plugnmeet_settings_cache()


def _apply(monkeypatch, **extra):
    for key, value in {**BASE_ENV, **extra}.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


def test_disabled_by_default():
    assert get_plugnmeet_settings().enabled is False


def test_full_config(monkeypatch):
    _apply(
        monkeypatch,
        LEARNHOUSE_PLUGNMEET_QUICK_ROOM="false",
        LEARNHOUSE_PLUGNMEET_WEBHOOK_ENABLED="yes",
        LEARNHOUSE_PLUGNMEET_EMPTY_TIMEOUT="5",
        LEARNHOUSE_PLUGNMEET_START_EARLY_MINUTES="15",
        LEARNHOUSE_PLUGNMEET_HTTP_TIMEOUT="500",
    )
    s = get_plugnmeet_settings()
    assert s.enabled
    assert s.server_url == "http://192.168.1.20:8080"
    assert s.public_url == "https://learn.example.com"
    assert s.quick_room is False and s.webhook_enabled is True
    assert s.empty_timeout == 60 and s.start_early_minutes == 15 and s.end_grace_minutes == 60
    assert s.http_timeout == 60.0
    assert "0123456789" not in repr(s)


def test_secret_from_file(monkeypatch, tmp_path):
    secret = tmp_path / "s"
    secret.write_text("from-file-secret-123456\n")
    _apply(monkeypatch, LEARNHOUSE_PLUGNMEET_API_SECRET=None, LEARNHOUSE_PLUGNMEET_API_SECRET_FILE=str(secret))
    assert get_plugnmeet_settings().api_secret == "from-file-secret-123456"


def test_empty_secret_file_disables(monkeypatch, tmp_path):
    secret = tmp_path / "s"
    secret.write_text("  ")
    _apply(monkeypatch, LEARNHOUSE_PLUGNMEET_API_SECRET=None, LEARNHOUSE_PLUGNMEET_API_SECRET_FILE=str(secret))
    assert get_plugnmeet_settings().enabled is False


def test_ca_bundle(monkeypatch, tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    _apply(monkeypatch, LEARNHOUSE_PLUGNMEET_CA_BUNDLE=str(ca))
    assert get_plugnmeet_settings().ca_bundle == str(ca)


def test_public_url_defaults_from_domain(monkeypatch):
    _apply(monkeypatch, LEARNHOUSE_PLUGNMEET_PUBLIC_URL=None)
    monkeypatch.setattr(cfg, "_default_public_url", lambda: "https://lh.example.com")
    assert get_plugnmeet_settings().public_url == "https://lh.example.com"


def test_public_url_required(monkeypatch):
    _apply(monkeypatch, LEARNHOUSE_PLUGNMEET_PUBLIC_URL=None)
    monkeypatch.setattr(cfg, "_default_public_url", lambda: None)
    assert get_plugnmeet_settings().enabled is False


def test_default_public_url(monkeypatch):
    from types import SimpleNamespace

    fake = SimpleNamespace(hosting_config=SimpleNamespace(domain="lh.local", ssl=True))
    monkeypatch.setattr("config.config.get_learnhouse_config", lambda: fake)
    assert cfg._default_public_url() == "https://lh.local"
    fake.hosting_config.domain = ""
    assert cfg._default_public_url() is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"LEARNHOUSE_PLUGNMEET_API_KEY": None},
        {"LEARNHOUSE_PLUGNMEET_API_SECRET": "short"},
        {"LEARNHOUSE_PLUGNMEET_API_SECRET_FILE": "/nonexistent/secret"},
        {"LEARNHOUSE_PLUGNMEET_SERVER_URL": "http://meet.example.com"},
        {"LEARNHOUSE_PLUGNMEET_SERVER_URL": "ftp://10.0.0.1"},
        {"LEARNHOUSE_PLUGNMEET_SERVER_URL": "https://u:p@meet.example.com"},
        {"LEARNHOUSE_PLUGNMEET_CLIENT_URL": "http://meet.example.com"},
        {"LEARNHOUSE_PLUGNMEET_CLIENT_URL": "https://meet.example.com/?x=1"},
        {"LEARNHOUSE_PLUGNMEET_PUBLIC_URL": "javascript:alert(1)"},
        {"LEARNHOUSE_PLUGNMEET_EMPTY_TIMEOUT": "soon"},
        {"LEARNHOUSE_PLUGNMEET_HTTP_TIMEOUT": "soon"},
        {"LEARNHOUSE_PLUGNMEET_CA_BUNDLE": "/nonexistent/ca.pem"},
    ],
)
def test_invalid_config_fails_closed(monkeypatch, overrides):
    _apply(monkeypatch, **overrides)
    assert get_plugnmeet_settings().enabled is False


def test_insecure_http_opt_in(monkeypatch):
    _apply(
        monkeypatch,
        LEARNHOUSE_PLUGNMEET_SERVER_URL="http://meet.example.com",
        LEARNHOUSE_PLUGNMEET_CLIENT_URL="http://meet.example.com",
        LEARNHOUSE_PLUGNMEET_ALLOW_INSECURE_HTTP="true",
    )
    assert get_plugnmeet_settings().enabled


@pytest.mark.parametrize(
    "host,private",
    [
        ("192.168.1.20", True),
        ("127.0.0.1", True),
        ("[::1]", True),
        ("169.254.1.1", True),
        ("8.8.8.8", False),
        ("pnm-server", True),
        ("host.docker.internal", True),
        ("tower.local", True),
        ("meet.example.com", False),
    ],
)
def test_is_private_host(host, private):
    assert is_private_host(host) is private
