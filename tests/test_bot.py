import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date, datetime
from unittest.mock import patch

import pytest

from src import bot
from src.bot import Request, answer, handle_update, parse_request, resolve_meal
from src.feedback import load_votes
from src.models import DiningHallMenu, MenuItem, Recommendation, ScrapeResult
from src.storage import menus_path, recommendation_path, save_json

TUE = date(2026, 10, 27)
FRI = date(2026, 10, 30)
SAT = date(2026, 10, 31)


# ------- parsing -------

@pytest.mark.parametrize("text, expected", [
    ("/now", Request("recommend", TUE)),
    ("/lunch", Request("recommend", TUE, "Lunch")),
    ("/dinner tomorrow", Request("recommend", date(2026, 10, 28), "Dinner")),
    ("/lunch@SDiningbot", Request("recommend", TUE, "Lunch")),
    ("/tomorrow", Request("recommend", date(2026, 10, 28))),
    ("/hall flomo", Request("hall", TUE, None, "florence moore")),
    ("/hall", Request("help", TUE)),
    ("/hours tomorrow", Request("hours", date(2026, 10, 28))),
    ("/stats", Request("stats", TUE)),
    ("/start", Request("help", TUE)),
    ("/nonsense", Request("help", TUE)),
    ("what's for dinner tomorrow?", Request("recommend", date(2026, 10, 28), "Dinner")),
    ("What's at Florence Moore for lunch", Request("hall", TUE, "Lunch", "florence moore")),
    ("arrillaga", Request("hall", TUE, None, "arrillaga")),
    ("when does wilbur open", Request("hours", TUE)),
    ("brunch", Request("recommend", TUE, "Lunch")),
    ("I'm hungry", Request("recommend", TUE)),
    ("hi", Request("help", TUE)),
])
def test_parse_request(text, expected):
    assert parse_request(text, TUE) == expected


def at(d, hour):
    return datetime(d.year, d.month, d.day, hour, 0)


def test_resolve_meal_by_time_of_day():
    assert resolve_meal(Request("recommend", TUE), at(TUE, 8)) == ("Breakfast", TUE, True)
    assert resolve_meal(Request("recommend", TUE), at(TUE, 12)) == ("Lunch", TUE, True)
    # After dinner, "now" means tomorrow's first meal.
    assert resolve_meal(Request("recommend", TUE), at(TUE, 22)) == ("Breakfast", date(2026, 10, 28), True)
    assert resolve_meal(Request("recommend", FRI), at(FRI, 22)) == ("Lunch", SAT, True)


def test_resolve_meal_weekend_breakfast_is_brunch():
    assert resolve_meal(Request("recommend", SAT, "Breakfast"), at(SAT, 8)) == ("Lunch", SAT, False)
    assert resolve_meal(Request("recommend", SAT, "Dinner"), at(SAT, 8)) == ("Dinner", SAT, False)


# ------- answering -------

def _rec(day="2026-10-27", meal="Lunch"):
    return Recommendation(best_hall="Stern Dining", backup_hall="Wilbur Dining",
                          recommended_plate=["Tofu Bowl"], avoid=[], confidence="high",
                          reasoning="Good tofu.", date=day, meal=meal)


def test_cached_recommendation_skips_scrape_and_ai(tmp_path):
    save_json(recommendation_path(str(tmp_path), "2026-10-27", "Lunch"), _rec().model_dump())
    with patch("src.bot.run_pipeline", side_effect=AssertionError("should not scrape")), \
         patch("src.bot.load_dining_hours_safe", return_value=None):
        text, markup = answer(Request("recommend", TUE, "Lunch"), at(TUE, 12), str(tmp_path))
    assert "Stern Dining" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == "fb:2026-10-27:Lunch:up"


def test_uncached_recommendation_runs_the_pipeline(tmp_path):
    with patch("src.bot.run_pipeline", return_value=(_rec(meal="Dinner"), None)) as run, \
         patch("src.bot.load_dining_hours_safe", return_value=None):
        text, _ = answer(Request("recommend", TUE, "Dinner"), at(TUE, 12), str(tmp_path))
    run.assert_called_once()
    assert run.call_args.args[:2] == ("Dinner", TUE)
    assert "Stern Dining" in text


def test_no_menus_posted_message(tmp_path):
    with patch("src.bot.run_pipeline", return_value=None):
        text, markup = answer(Request("recommend", SAT, "Dinner"), at(TUE, 12), str(tmp_path))
    assert "No dinner menus posted for Saturday" in text
    assert markup is None


def test_scrape_failure_is_explained_not_raised(tmp_path):
    with patch("src.bot.run_pipeline", side_effect=bot.ScrapeFailure("Date '11/9/2026' not available")):
        text, _ = answer(Request("recommend", date(2026, 11, 9), "Lunch"), at(TUE, 12), str(tmp_path))
    assert "Couldn't get Monday's menus" in text


def test_hall_menu_marks_dishes(tmp_path):
    menu = DiningHallMenu(hall_name="Florence Moore Dining", hall_id="fm", date="10/27/2026", meal="Lunch",
                          items=[MenuItem(name="Grilled Chicken"), MenuItem(name="Fried Fish"),
                                 MenuItem(name="Plain Rolls")])
    scrape = ScrapeResult(date="2026-10-27", meal="Lunch", halls=[menu], scraped_at="x")
    save_json(menus_path(str(tmp_path), "2026-10-27", "Lunch"), scrape.model_dump())
    with patch("src.bot.load_dining_hours_safe", return_value=None):
        text, _ = answer(Request("hall", TUE, "Lunch", "florence moore"), at(TUE, 12), str(tmp_path))
    lines = text.splitlines()
    assert "✅ Grilled Chicken" in lines
    assert "⛔ Fried Fish" in lines
    assert "▫️ Plain Rolls" in lines
    assert lines.index("✅ Grilled Chicken") < lines.index("▫️ Plain Rolls") < lines.index("⛔ Fried Fish")


# ------- Telegram plumbing -------

def test_strangers_are_ignored(tmp_path):
    with patch("src.bot.send_message") as send, patch("src.bot.call_api") as api:
        handle_update({"message": {"chat": {"id": 666}, "text": "/now"}}, {"42"}, str(tmp_path))
    send.assert_not_called()
    api.assert_not_called()


def test_strangers_cannot_vote(tmp_path):
    cq = {"id": "c1", "data": "fb:2026-10-27:Lunch:up", "message": {"chat": {"id": 666}, "message_id": 5}}
    with patch("src.bot.call_api"):
        handle_update({"callback_query": cq}, {"42"}, str(tmp_path))
    assert load_votes(str(tmp_path)) == []


def test_vote_is_recorded_and_button_marked(tmp_path):
    save_json(recommendation_path(str(tmp_path), "2026-10-27", "Lunch"), _rec().model_dump())
    cq = {"id": "c1", "data": "fb:2026-10-27:Lunch:down", "message": {"chat": {"id": 42}, "message_id": 5}}
    with patch("src.bot.call_api") as api:
        handle_update({"callback_query": cq}, {"42"}, str(tmp_path))
    [vote] = load_votes(str(tmp_path))
    assert (vote["vote"], vote["best_hall"]) == ("down", "Stern Dining")
    methods = [c.args[0] for c in api.call_args_list]
    assert methods == ["answerCallbackQuery", "editMessageReplyMarkup"]
    buttons = api.call_args_list[1].args[1]["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["👍", "✓ 👎"]


def test_message_gets_a_reply(tmp_path):
    with patch("src.bot.send_message") as send, patch("src.bot.call_api"):
        handle_update({"message": {"chat": {"id": 42}, "text": "/help"}}, {"42"}, str(tmp_path),
                      now_la=at(TUE, 12))
    send.assert_called_once()
    assert send.call_args.args[0] == "42"
    assert "/hall" in send.call_args.args[1]


def test_offset_round_trip(tmp_path):
    path = tmp_path / "bot_state.json"
    assert bot._load_offset(path) is None
    bot._save_offset(path, 1234)
    assert bot._load_offset(path) == 1234
