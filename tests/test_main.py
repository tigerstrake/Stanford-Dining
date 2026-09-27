import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
from datetime import datetime
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.main import MEAL_FALLBACKS, _detect_meal, main
from src.models import DiningHallMenu, MenuItem


def la(y, m, d, hour):
    return datetime(y, m, d, hour, 30)


# ------- meal detection -------

def test_weekday_detection():
    wed = (2026, 9, 30)
    assert _detect_meal(la(*wed, 8)) == "Breakfast"
    assert _detect_meal(la(*wed, 12)) == "Lunch"
    assert _detect_meal(la(*wed, 18)) == "Dinner"


def test_weekend_never_returns_brunch():
    # The menu site has no items under "Brunch" on any date; weekend daytime is "Lunch".
    for day in ((2026, 9, 26), (2026, 9, 27)):  # Sat, Sun
        assert _detect_meal(la(*day, 8)) == "Lunch"
        assert _detect_meal(la(*day, 12)) == "Lunch"
        assert _detect_meal(la(*day, 14)) == "Lunch"
        assert _detect_meal(la(*day, 18)) == "Dinner"


def test_brunch_falls_back_to_lunch_first():
    assert MEAL_FALLBACKS["Brunch"][0] == "Lunch"


# ------- CLI behaviour with a fake scraper -------

def _menu(hall, meal, items):
    return DiningHallMenu(
        hall_name=f"{hall} Dining", hall_id=hall, date="9/27/2026", meal=meal,
        items=[MenuItem(name=n, ingredients="") for n in items],
    )


class FakeScraper:
    """scrape_all returns items only for the meals listed in `stocked`."""
    calls = []

    def __init__(self, *a, **kw):
        pass

    def scrape_all(self, date_str, meal, hall_ids=None):
        FakeScraper.calls.append(meal)
        items = ["Grilled Chicken", "Black Beans", "Broccoli"] if meal in self.stocked else []
        return [_menu(h, meal, items) for h in ("Arrillaga", "Stern", "Wilbur")]


@pytest.fixture
def cli(tmp_path):
    FakeScraper.calls = []
    runner = CliRunner()
    with patch("src.main.StanfordMenuScraper", FakeScraper), \
         patch("src.main.load_dining_hours_safe", return_value=None), \
         patch("src.main.notify") as notify:
        def run(*args, stocked):
            FakeScraper.stocked = set(stocked)
            return runner.invoke(main, ["--no-ai", "--data-dir", str(tmp_path), *args]), notify
        yield run


def test_auto_falls_back_when_detected_meal_is_empty(cli, caplog):
    with patch("src.main._detect_meal", return_value="Brunch"):
        result, notify = cli("--meal", "auto", stocked={"Lunch"})
    assert result.exit_code == 0, result.output
    assert FakeScraper.calls == ["Brunch", "Lunch"]
    notify.assert_called_once()
    assert notify.call_args.args[0].meal == "Lunch"


def test_no_menus_at_all_exits_cleanly_without_notifying(cli, caplog):
    caplog.set_level(logging.WARNING)
    with patch("src.main._detect_meal", return_value="Brunch"):
        result, notify = cli("--meal", "auto", stocked=set())
    assert result.exit_code == 0, result.output
    assert FakeScraper.calls == ["Brunch", "Lunch", "Breakfast"]
    notify.assert_not_called()
    assert "No menus posted" in caplog.text


def test_explicit_meal_is_never_swapped(cli):
    result, notify = cli("--meal", "brunch", stocked={"Lunch"})
    assert result.exit_code == 0, result.output
    assert FakeScraper.calls == ["Brunch"]
    notify.assert_not_called()


def test_structural_scrape_failure_still_exits_1(cli):
    with patch.object(FakeScraper, "scrape_all", side_effect=RuntimeError("page changed")):
        result, notify = cli("--meal", "lunch", stocked={"Lunch"})
    assert result.exit_code == 1
    notify.assert_not_called()
