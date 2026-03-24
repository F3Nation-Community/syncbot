"""slack-manifest.json stays aligned with syncbot/slack_manifest_scopes.py."""

import json
import re
from pathlib import Path

from slack_manifest_scopes import (
    BOT_SCOPES,
    USER_SCOPES,
    bot_scopes_comma_separated,
    user_scopes_comma_separated,
)


def _manifest() -> dict:
    root = Path(__file__).resolve().parent.parent
    return json.loads(root.joinpath("slack-manifest.json").read_text(encoding="utf-8"))


def test_slack_manifest_bot_scopes_match_constants():
    bot = _manifest()["oauth_config"]["scopes"]["bot"]
    assert bot == list(BOT_SCOPES)


def test_slack_manifest_user_scopes_match_constants():
    user = _manifest()["oauth_config"]["scopes"]["user"]
    assert user == list(USER_SCOPES)


def test_sam_template_slack_oauth_default_matches_bot_scopes():
    """infra/aws/template.yaml SlackOauthBotScopes Default must match BOT_SCOPES."""
    root = Path(__file__).resolve().parent.parent
    text = root.joinpath("infra/aws/template.yaml").read_text(encoding="utf-8")
    m = re.search(
        r'^\s*SlackOauthBotScopes:\s*\n(?:^\s+.*\n)*?\s*Default:\s*"([^"]+)"',
        text,
        re.MULTILINE,
    )
    assert m, "SlackOauthBotScopes Default not found in template.yaml"
    assert m.group(1) == bot_scopes_comma_separated()


def test_sam_template_slack_user_oauth_default_matches_user_scopes():
    """infra/aws/template.yaml SlackOauthUserScopes Default must match USER_SCOPES."""
    root = Path(__file__).resolve().parent.parent
    text = root.joinpath("infra/aws/template.yaml").read_text(encoding="utf-8")
    m = re.search(
        r'^\s*SlackOauthUserScopes:\s*\n(?:^\s+.*\n)*?\s*Default:\s*"([^"]*)"',
        text,
        re.MULTILINE,
    )
    assert m, "SlackOauthUserScopes Default not found in template.yaml"
    assert m.group(1) == user_scopes_comma_separated()


def test_bot_scopes_comma_separated_roundtrip():
    assert bot_scopes_comma_separated().split(",") == list(BOT_SCOPES)


def test_user_scopes_comma_separated_roundtrip():
    s = user_scopes_comma_separated()
    assert [x.strip() for x in s.split(",") if x.strip()] == list(USER_SCOPES)
