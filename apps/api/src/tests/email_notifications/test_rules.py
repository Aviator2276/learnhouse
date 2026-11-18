"""Levels, mentions and text extraction."""

from datetime import datetime, timezone

import pytest

from src.services.email_notifications import config, rules
from src.services.email_notifications.rules import Preference

from .conftest import doc


@pytest.mark.parametrize(
    "level, expected",
    [
        ("off", set()),
        ("mentions", {"mention"}),
        ("replies", {"mention", "reply"}),
        ("highlights", {"mention", "reply", "staff"}),
        ("all", {"mention", "reply", "staff", "new"}),
    ],
)
def test_each_level_lets_through_exactly_its_reasons(level, expected):
    pref = Preference(level=level)
    got = {reason for reason in ("mention", "reply", "staff", "new") if rules.wants(pref, reason, 1, "general")}
    assert got == expected


def test_default_level_is_highlights():
    assert Preference().level == "highlights" == rules.DEFAULT_LEVEL


def test_muted_community_is_silent_even_for_a_mention():
    pref = Preference(level="all", muted_communities=frozenset({7}))
    assert not rules.wants(pref, "mention", 7, "general")
    assert rules.wants(pref, "mention", 8, "general")


def test_muted_post_type_hides_news_but_not_mentions_or_replies():
    pref = Preference(level="all", muted_labels=frozenset({"showcase", "general"}))
    assert not rules.wants(pref, "new", 1, "showcase")
    assert not rules.wants(pref, "staff", 1, "showcase")
    assert not rules.wants(pref, "new", 1, None)  # no label counts as general
    assert rules.wants(pref, "mention", 1, "showcase")
    assert rules.wants(pref, "reply", 1, "showcase")
    assert rules.wants(pref, "new", 1, "question")


def test_digest_follows_level_and_switch():
    assert rules.wants_digest(Preference())
    assert rules.wants_digest(Preference(level="mentions"))
    assert not rules.wants_digest(Preference(level="off"))
    assert not rules.wants_digest(Preference(weekly_digest=False))


def test_mentions_are_found_and_lowercased():
    text = "Thanks @Sam and (@kim-lee), also @a.b_c!"
    assert {"sam", "kim-lee", "a.b_c"} <= rules.mentioned_usernames(text)


def test_mention_at_the_end_of_a_sentence_matches_without_the_full_stop():
    assert "sam" in rules.mentioned_usernames("thanks @sam.")


def test_email_addresses_and_urls_are_not_mentions():
    text = "mail sam@example.com or see https://example.com/@kim and x/@lee and @@max"
    assert rules.mentioned_usernames(text) == set()


def test_no_text_no_mentions():
    assert rules.mentioned_usernames("") == set()
    assert rules.mentioned_usernames(None) == set()


def test_plain_text_reads_editor_json_and_legacy_text():
    assert rules.plain_text(doc("Hello   @sam\nthere")) == "Hello @sam there"
    assert rules.plain_text("just  text") == "just text"
    assert rules.plain_text(None) == ""


def test_parse_created_handles_what_upstream_writes():
    assert rules.parse_created("2026-11-16 12:00:00.123456") == datetime(2026, 11, 16, 12, 0, 0, 123456)
    aware = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    parsed = rules.parse_created(aware.isoformat())
    assert parsed.tzinfo is None and parsed == aware.astimezone().replace(tzinfo=None)
    assert rules.parse_created("") is None
    assert rules.parse_created(None) is None
    assert rules.parse_created("yesterday") is None


def test_config_defaults(monkeypatch):
    monkeypatch.delenv(config.ENV_DISABLED, raising=False)
    monkeypatch.delenv(config.ENV_DELAY, raising=False)
    assert config.get_config() == config.CommunityEmailConfig(enabled=True, delay_minutes=10)


@pytest.mark.parametrize("raw, delay", [("0", 0), ("45", 45), ("-5", 0), ("99999", 1440), ("soon", 10), (" ", 10)])
def test_config_delay_is_clamped(monkeypatch, raw, delay):
    monkeypatch.setenv(config.ENV_DELAY, raw)
    assert config.get_config().delay_minutes == delay


@pytest.mark.parametrize("raw, enabled", [("1", False), ("TRUE", False), ("yes", False), ("0", True), ("", True)])
def test_config_disable_switch(monkeypatch, raw, enabled):
    monkeypatch.setenv(config.ENV_DISABLED, raw)
    assert config.get_config().enabled is enabled
