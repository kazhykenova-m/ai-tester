import pytest

from ai_tester.agent import is_allowed_url, parse_action


def test_parse_plain_json():
    assert parse_action('{"action":"click","id":3}')["id"] == 3


def test_parse_fenced_json():
    text = '```json\n{"action":"done","success":true,"reason":"ok"}\n```'
    assert parse_action(text)["success"] is True


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        parse_action('{"action":"delete_everything"}')


def test_not_json_rejected():
    with pytest.raises(ValueError):
        parse_action("hello")


def test_navigation_limited_to_start_site():
    start = "https://www.saucedemo.com"
    assert is_allowed_url("https://www.saucedemo.com/cart.html", start)
    assert not is_allowed_url("https://evil.example.com", start)
