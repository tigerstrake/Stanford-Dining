import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import MagicMock, patch

import pytest

from src.models import Recommendation
from src.notifier import (
    TELEGRAM_MAX_MESSAGE_CHARS,
    _truncate,
    format_recommendation_html,
    format_recommendation_plain,
    notify,
    send_telegram,
)


def make_rec(**overrides) -> Recommendation:
    base = dict(
        best_hall="Ricker Dining",
        backup_hall="Wilbur Dining",
        recommended_plate=["Grilled Chicken", "Black Eyed Peas"],
        avoid=["Blackened Salmon"],
        confidence="high",
        reasoning="Ricker has the best protein options.",
        ai_generated=False,
        date="2026-09-25",
        meal="Lunch",
    )
    base.update(overrides)
    return Recommendation(**base)


def fake_response(status=200, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    if body is None:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = body
    return resp


# ------- formatting -------

def test_html_contains_all_sections():
    msg = format_recommendation_html(make_rec())
    assert "<b>Best hall:</b> Ricker Dining" in msg
    assert "<b>Backup hall:</b> Wilbur Dining" in msg
    assert "HIGH" in msg
    assert "• Grilled Chicken" in msg
    assert "<b>Skip</b>" in msg
    assert "• Blackened Salmon" in msg
    assert "deterministic scoring" in msg


def test_html_omits_ai_footer_when_ai_generated():
    msg = format_recommendation_html(make_rec(ai_generated=True))
    assert "deterministic scoring" not in msg


def test_html_omits_skip_section_when_empty():
    msg = format_recommendation_html(make_rec(avoid=[]))
    assert "<b>Skip</b>" not in msg


def test_html_escapes_scraped_text():
    # Dish names come from a third-party page; markup must never reach Telegram raw.
    rec = make_rec(recommended_plate=["Mac & Cheese <b>bold</b>"], reasoning="a < b")
    msg = format_recommendation_html(rec)
    assert "Mac &amp; Cheese &lt;b&gt;bold&lt;/b&gt;" in msg
    assert "a &lt; b" in msg
    assert "<b>bold</b>" not in msg


def test_plain_has_no_html():
    msg = format_recommendation_plain(make_rec())
    assert "<b>" not in msg
    assert "Best hall:    Ricker Dining" in msg


def test_truncate_respects_limit_and_line_boundary():
    long_text = "\n".join(f"line {i} " + "x" * 50 for i in range(200))
    out = _truncate(long_text)
    assert len(out) <= TELEGRAM_MAX_MESSAGE_CHARS
    assert out.endswith("…")
    # Cut happened on a line boundary: the char before the ellipsis ends a line.
    assert out[:-1].endswith("x")


def test_truncate_leaves_short_text_alone():
    assert _truncate("hello") == "hello"


# ------- send_telegram -------

def test_send_skips_without_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    with patch("src.notifier.requests.post") as post:
        assert send_telegram(make_rec()) is False
        post.assert_not_called()


def test_send_skips_without_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    with patch("src.notifier.requests.post") as post:
        assert send_telegram(make_rec()) is False
        post.assert_not_called()


def test_send_posts_html_to_bot_api(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    with patch("src.notifier.requests.post", return_value=fake_response(200, {"ok": True})) as post:
        assert send_telegram(make_rec()) is True
    post.assert_called_once()
    url = post.call_args.args[0]
    payload = post.call_args.kwargs["json"]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload["chat_id"] == "42"
    assert payload["parse_mode"] == "HTML"
    assert "Ricker Dining" in payload["text"]
    assert len(payload["text"]) <= TELEGRAM_MAX_MESSAGE_CHARS


def test_send_fans_out_to_multiple_chats(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", " 1, 2 ,,3 ")
    with patch("src.notifier.requests.post", return_value=fake_response(200, {"ok": True})) as post:
        assert send_telegram(make_rec()) is True
    assert [c.kwargs["json"]["chat_id"] for c in post.call_args_list] == ["1", "2", "3"]


def test_send_reports_failure_when_telegram_says_not_ok(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    bad = fake_response(400, {"ok": False, "description": "Bad Request: chat not found"})
    with patch("src.notifier.requests.post", return_value=bad):
        assert send_telegram(make_rec()) is False


def test_send_survives_network_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    with patch("src.notifier.requests.post", side_effect=ConnectionError("boom")):
        assert send_telegram(make_rec()) is False


def test_send_partial_success_counts_as_delivered(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1,2")
    responses = [fake_response(400, {"ok": False, "description": "chat not found"}),
                 fake_response(200, {"ok": True})]
    with patch("src.notifier.requests.post", side_effect=responses):
        assert send_telegram(make_rec()) is True


# ------- notify -------

def test_notify_dry_run_prints_but_does_not_send(capsys):
    with patch("src.notifier.send_telegram") as send:
        notify(make_rec(), dry_run=True)
        send.assert_not_called()
    out = capsys.readouterr().out
    assert "Stanford Dining Recommendation" in out


def test_notify_sends_when_not_dry_run():
    with patch("src.notifier.send_telegram", return_value=True) as send:
        notify(make_rec(), dry_run=False)
        send.assert_called_once()
