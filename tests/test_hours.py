import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from src.hours import (
    day_summary,
    fetch_dining_hours,
    hall_key,
    hours_for_meal,
    is_weekend,
    load_dining_hours_safe,
    lookup,
    parse_hours_html,
)
from src.models import DiningHours, HallHours, Recommendation
from src.notifier import format_recommendation_html, format_recommendation_plain

FIXTURE = Path(__file__).parent / "fixtures" / "dining_hours_2026_09_25.html"

# Hall names exactly as the menu page's location dropdown spells them.
MENU_HALL_NAMES = [
    "Arrillaga Family Dining Commons", "Branner Dining", "EVGR Dining",
    "Florence Moore Dining", "Gerhard Casper Dining", "Lakeside Dining",
    "Ricker Dining", "Stern Dining", "Wilbur Dining",
]


@pytest.fixture(scope="module")
def halls():
    return parse_hours_html(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def hours(halls):
    return DiningHours(fetched_at="2026-09-25T00:00:00+00:00", source_url="fixture", halls=halls)


# ------- parsing -------

def test_parses_eight_halls_with_hours(halls):
    assert len(halls) == 8
    for name in ("arrillaga", "lakeside", "wilbur", "stern", "florence moore",
                 "ricker", "gerhard casper", "branner"):
        assert name in halls, name


def test_arrillaga_weekday_weekend_and_late_night(halls):
    a = halls["arrillaga"]
    assert a.weekday == {
        "Breakfast": "7:30 a.m. - 10:00 a.m.",
        "Lunch": "11:00 a.m. - 3:00 p.m.",
        "Dinner": "5:00 p.m. - 8:30 p.m.",
    }
    assert a.weekend == {
        "Brunch/Lunch": "9:30 a.m. - 1:30 p.m.",
        "Dinner": "5:00 p.m. - 8:00 p.m.",
    }
    assert a.late_night == "Arrillaga Nights (Tuesday - Saturday): 9:00 p.m. - 2:00 a.m."
    assert a.note == "Fall Hours Begin Friday, September 18th"


def test_lakeside_late_night_label(halls):
    lk = halls["lakeside"]
    assert lk.late_night.startswith("Late Night at Lakeside")
    assert lk.late_night.endswith("(Sunday - Thursday): 9:00 p.m. - 2:00 a.m.")
    assert lk.weekday["Lunch"] == "11:00 a.m. - 2:00 p.m."


def test_branner_has_no_breakfast_and_no_weekend(halls):
    b = halls["branner"]
    assert "Breakfast" not in b.weekday
    assert b.weekday["Dinner"] == "5:00 p.m. - 7:00 p.m."
    assert b.weekend == {}
    assert b.late_night == ""


def test_addresses_and_blurbs_are_not_mistaken_for_notes(halls):
    for h in halls.values():
        assert "Stanford, CA" not in h.note
        assert "Home of" not in h.note


def test_malformed_html_yields_nothing():
    assert parse_hours_html("<html><body><p>nope</p></body></html>") == {}
    assert parse_hours_html("") == {}


# ------- name mapping -------

def test_hall_key_normalises_menu_and_hours_spellings():
    assert hall_key("Gerhard Casper Dining") == hall_key("Gerhard Casper Dining Commons")
    assert hall_key("Arrillaga Family Dining Commons") == "arrillaga"
    assert hall_key("Florence Moore Dining") == "florence moore"


def test_every_menu_hall_except_evgr_has_hours(hours):
    for name in MENU_HALL_NAMES:
        found = lookup(hours, name)
        if name == "EVGR Dining":
            assert found is None  # not a public dining hall on the hours page
        else:
            assert found is not None, name


def test_lookup_handles_none_and_unknown(hours):
    assert lookup(None, "Ricker Dining") is None
    assert lookup(hours, "Nonexistent Hall") is None


# ------- meal window -------

def test_hours_for_meal_weekday(hours):
    r = lookup(hours, "Ricker Dining")
    assert hours_for_meal(r, "Lunch", weekend=False) == "11:00 a.m. - 1:30 p.m."
    assert hours_for_meal(r, "Breakfast", weekend=False) == "7:30 a.m. - 9:00 a.m."
    assert hours_for_meal(r, "Brunch", weekend=False) is None


def test_hours_for_meal_weekend_brunch_slash_lunch(hours):
    r = lookup(hours, "Ricker Dining")
    assert hours_for_meal(r, "Brunch", weekend=True) == "10:30 a.m. - 1:30 p.m."
    assert hours_for_meal(r, "Lunch", weekend=True) == "10:30 a.m. - 1:30 p.m."
    assert hours_for_meal(r, "Breakfast", weekend=True) is None


def test_is_weekend():
    assert is_weekend(date(2026, 9, 25)) is False   # Friday
    assert is_weekend(date(2026, 9, 26)) is True    # Saturday


def test_day_summary(hours):
    a = lookup(hours, "Arrillaga Family Dining Commons")
    s = day_summary(a, weekend=False)
    assert s.startswith("Breakfast 7:30 a.m. - 10:00 a.m. · Lunch 11:00 a.m. - 3:00 p.m. · Dinner 5:00 p.m. - 8:30 p.m.")
    assert "Arrillaga Nights" in s
    assert day_summary(lookup(hours, "Branner Dining"), weekend=True) == ""


# ------- fetch -------

def test_fetch_raises_when_page_has_no_halls():
    class R:
        text = "<html></html>"
        def raise_for_status(self): pass
    with patch("src.hours.requests.get", return_value=R()):
        with pytest.raises(RuntimeError):
            fetch_dining_hours()


def test_load_safe_returns_none_on_network_error():
    with patch("src.hours.requests.get", side_effect=ConnectionError("down")):
        assert load_dining_hours_safe() is None


def test_load_safe_returns_hours_from_fixture():
    class R:
        text = FIXTURE.read_text(encoding="utf-8")
        def raise_for_status(self): pass
    with patch("src.hours.requests.get", return_value=R()):
        h = load_dining_hours_safe()
    assert h is not None and len(h.halls) == 8


# ------- message rendering -------

def make_rec(**overrides) -> Recommendation:
    base = dict(
        best_hall="Ricker Dining", backup_hall="Arrillaga Family Dining Commons",
        recommended_plate=["Grilled Chicken"], avoid=[], confidence="high",
        reasoning="r", ai_generated=True, date="2026-09-25", meal="Lunch",
    )
    base.update(overrides)
    return Recommendation(**base)


def test_html_includes_meal_window_and_hours_section(hours):
    msg = format_recommendation_html(make_rec(), hours)
    assert "<b>Best hall:</b> Ricker Dining (Lunch 11:00 a.m. - 1:30 p.m.)" in msg
    assert "<b>Backup hall:</b> Arrillaga Family Dining Commons (Lunch 11:00 a.m. - 3:00 p.m.)" in msg
    assert "🕒 <b>Hours Friday</b>" in msg
    assert "<b>Ricker Dining:</b> Breakfast 7:30 a.m. - 9:00 a.m. · Lunch 11:00 a.m. - 1:30 p.m. · Dinner 5:00 p.m. - 8:00 p.m." in msg
    assert "<i>Fall Hours Begin Friday, September 18th</i>" in msg


def test_html_weekend_uses_weekend_block(hours):
    msg = format_recommendation_html(make_rec(date="2026-09-26", meal="Brunch"), hours)
    assert "Ricker Dining (Brunch 10:30 a.m. - 1:30 p.m.)" in msg
    assert "<b>Hours Saturday</b>" in msg
    assert "Breakfast" not in msg.split("Hours Saturday")[1]


def test_html_without_hours_has_no_hours_section(hours):
    msg = format_recommendation_html(make_rec(), None)
    assert "Hours" not in msg
    assert "<b>Best hall:</b> Ricker Dining\n" in msg


def test_hall_without_hours_is_skipped_gracefully(hours):
    msg = format_recommendation_html(make_rec(best_hall="EVGR Dining"), hours)
    assert "<b>Best hall:</b> EVGR Dining\n" in msg
    assert "<b>Arrillaga Family Dining Commons:</b>" in msg


def test_plain_includes_hours(hours):
    msg = format_recommendation_plain(make_rec(), hours)
    assert "Best hall:    Ricker Dining (Lunch 11:00 a.m. - 1:30 p.m.)" in msg
    assert "Hours Friday:" in msg
    assert "  Ricker Dining: Breakfast" in msg
