import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pathlib import Path
import pytest
from src.scraper import parse_menu_html, _extract_locations, _extract_day_options, _extract_meal_options
from bs4 import BeautifulSoup

FIXTURE_DIR = Path(__file__).parent / "fixtures"
WILBUR_FIXTURE = FIXTURE_DIR / "wilbur_lunch_2026_05_11.html"


@pytest.fixture
def wilbur_html():
    return WILBUR_FIXTURE.read_text(encoding="utf-8")


def test_parse_fixture_finds_items(wilbur_html):
    menu = parse_menu_html(
        wilbur_html,
        hall_id="Wilbur",
        hall_name="Wilbur Dining",
        date="5/11/2026",
        meal="Lunch",
    )
    assert len(menu.items) > 0, "Should find menu items in fixture"
    assert menu.hall_name == "Wilbur Dining"
    assert menu.meal == "Lunch"


def test_parse_fixture_item_names(wilbur_html):
    menu = parse_menu_html(
        wilbur_html, hall_id="Wilbur", hall_name="Wilbur Dining",
        date="5/11/2026", meal="Lunch",
    )
    names = [it.name for it in menu.items]
    assert "Grilled Chicken" in names
    assert "Black Eyed Peas" in names
    assert "Blackened Salmon" in names


def test_parse_fixture_ingredients(wilbur_html):
    menu = parse_menu_html(
        wilbur_html, hall_id="Wilbur", hall_name="Wilbur Dining",
        date="5/11/2026", meal="Lunch",
    )
    salmon = next(it for it in menu.items if it.name == "Blackened Salmon")
    assert "salmon" in salmon.ingredients.lower()
    assert salmon.allergens != ""
    assert "FISH" in salmon.allergens.upper()


def test_parse_fixture_dietary_flags(wilbur_html):
    menu = parse_menu_html(
        wilbur_html, hall_id="Wilbur", hall_name="Wilbur Dining",
        date="5/11/2026", meal="Lunch",
    )
    tofu = next(it for it in menu.items if "Tofu" in it.name)
    assert tofu.is_vegan, "Cajun Tofu should be vegan"
    assert tofu.is_gluten_free, "Cajun Tofu should be gluten free"


def test_extract_locations_from_fixture(wilbur_html):
    soup = BeautifulSoup(wilbur_html, "lxml")
    locations = _extract_locations(soup)
    assert len(locations) > 0
    hall_ids = [loc[0] for loc in locations]
    assert "Wilbur" in hall_ids
    assert "Arrillaga" in hall_ids


def test_extract_days_from_fixture(wilbur_html):
    soup = BeautifulSoup(wilbur_html, "lxml")
    days = _extract_day_options(soup)
    assert len(days) == 7, "Should have 7 days in dropdown"
    assert "5/11/2026" in days


def test_extract_meals_from_fixture(wilbur_html):
    soup = BeautifulSoup(wilbur_html, "lxml")
    meals = _extract_meal_options(soup)
    assert "Breakfast" in meals
    assert "Lunch" in meals
    assert "Dinner" in meals
    assert "Brunch" in meals


def test_parse_empty_html():
    """Scraper should not crash on empty or malformed HTML."""
    menu = parse_menu_html(
        "<html><body></body></html>",
        hall_id="Test",
        hall_name="Test Dining",
        date="5/11/2026",
        meal="Lunch",
    )
    assert menu.items == []
