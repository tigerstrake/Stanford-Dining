import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from src.models import DiningHallMenu, MenuItem
from src.scorer import score_item, score_hall, score_all_halls, build_deterministic_recommendation


def make_item(name: str, ingredients: str = "", allergens: str = "") -> MenuItem:
    return MenuItem(name=name, ingredients=ingredients, allergens=allergens)


# ------- score_item -------

def test_grilled_chicken_scores_positive():
    item = make_item("Grilled Chicken", "chicken breast, olive oil, salt, pepper")
    score, tags = score_item(item)
    assert score > 0
    assert any("protein" in t or "meat" in t for t in tags)


def test_salmon_penalized():
    item = make_item("Blackened Salmon", "salmon, cajun spice, canola oil")
    score, tags = score_item(item)
    assert score < 0
    assert any("fish" in t for t in tags)


def test_fried_chicken_penalized():
    item = make_item("Fried Chicken", "chicken, flour, oil")
    score, tags = score_item(item)
    assert any("fried" in t for t in tags)
    # Might still get positive protein tag, but fried penalty applies
    assert any("-fried" in t for t in tags)


def test_vegetables_score_positive():
    item = make_item("Seasonal Steamed Vegetables", "broccoli, zucchini, carrots, salt")
    score, tags = score_item(item)
    assert score > 0
    assert any("vegetable" in t for t in tags)


def test_black_eyed_peas_legumes():
    item = make_item("Black Eyed Peas", "black eyed peas, garlic, onion, salt")
    score, tags = score_item(item)
    assert score > 0
    assert any("legume" in t for t in tags)


def test_brownie_penalized():
    item = make_item("Chocolate Brownie", "flour, sugar, cocoa, butter, eggs")
    score, tags = score_item(item)
    assert score < 0
    assert any("dessert" in t for t in tags)


def test_quinoa_whole_grain():
    item = make_item("Quinoa Bowl", "quinoa, roasted vegetables, lemon, olive oil")
    score, tags = score_item(item)
    assert score > 0
    assert any("grain" in t or "protein" in t for t in tags)


def test_hot_dog_ultra_processed():
    item = make_item("Hot Dog", "pork, mechanically separated chicken, sodium")
    score, tags = score_item(item)
    assert score < 0
    assert any("ultra_processed" in t for t in tags)


def test_scrambled_eggs_penalized():
    item = make_item("Scrambled Eggs", "eggs, butter, salt")
    score, tags = score_item(item)
    assert score < 0
    assert any("egg" in t for t in tags)


# ------- score_hall -------

def test_empty_hall_scores_zero():
    menu = DiningHallMenu(hall_name="Test", hall_id="test", date="2026-05-11", meal="Lunch", items=[])
    scored = score_hall(menu)
    assert scored.total_score == 0.0
    assert scored.item_count == 0


def test_hall_with_good_items_scores_well():
    items = [
        make_item("Grilled Chicken", "chicken breast, olive oil"),
        make_item("Roasted Broccoli", "broccoli, garlic, olive oil"),
        make_item("Lentil Soup", "lentils, carrots, celery, onion"),
        make_item("Brown Rice", "brown rice, salt"),
    ]
    menu = DiningHallMenu(hall_name="Test", hall_id="test", date="2026-05-11", meal="Lunch", items=items)
    scored = score_hall(menu)
    assert scored.total_score > 10
    assert len(scored.top_items) > 0


def test_hall_with_bad_items_scores_poorly():
    items = [
        make_item("Fried Chicken", "chicken, flour, oil, deep fried"),
        make_item("Chocolate Cake", "sugar, flour, cocoa, butter"),
        make_item("Hot Dog", "pork frankfurter"),
        make_item("Salmon Fillet", "salmon, butter"),
    ]
    menu = DiningHallMenu(hall_name="Test", hall_id="test", date="2026-05-11", meal="Lunch", items=items)
    scored = score_hall(menu)
    assert scored.total_score < 0


# ------- score_all_halls -------

def test_score_all_sorts_by_score():
    good_items = [make_item("Grilled Chicken", "chicken breast"), make_item("Kale Salad", "kale, lemon")]
    bad_items = [make_item("Fried Shrimp", "shrimp, fried"), make_item("Cake", "sugar, flour")]

    menus = [
        DiningHallMenu(hall_name="Bad", hall_id="bad", date="2026-05-11", meal="Lunch", items=bad_items),
        DiningHallMenu(hall_name="Good", hall_id="good", date="2026-05-11", meal="Lunch", items=good_items),
    ]
    scored = score_all_halls(menus)
    assert scored[0].hall_name == "Good"
    assert scored[1].hall_name == "Bad"


# ------- build_deterministic_recommendation -------

def test_deterministic_recommendation_picks_best_hall():
    good = DiningHallMenu(
        hall_name="BestHall", hall_id="best", date="2026-05-11", meal="Lunch",
        items=[make_item("Grilled Chicken", "chicken breast"), make_item("Brown Rice", "brown rice")]
    )
    ok = DiningHallMenu(
        hall_name="OkHall", hall_id="ok", date="2026-05-11", meal="Lunch",
        items=[make_item("Pasta", "white pasta, sauce")]
    )
    scored = score_all_halls([good, ok])
    rec = build_deterministic_recommendation(scored, date="2026-05-11", meal="Lunch")
    assert rec.best_hall == "BestHall"
    assert rec.backup_hall == "OkHall"
    assert len(rec.recommended_plate) > 0
    assert not rec.ai_generated


def test_deterministic_recommendation_no_items():
    menus = [
        DiningHallMenu(hall_name="Empty", hall_id="e", date="2026-05-11", meal="Lunch", items=[])
    ]
    scored = score_all_halls(menus)
    rec = build_deterministic_recommendation(scored, date="2026-05-11", meal="Lunch")
    assert rec.best_hall == "None"
    assert rec.confidence == "low"


# ------- breakfast (real items from Arrillaga, 2026-10-27) -------

def _breakfast_line():
    return [
        make_item("Steel Cut Oatmeal", "steel cut oats"),
        make_item("Seasonal Fruit", "seasonal fresh fruit"),
        make_item("Scrambled Eggs", "eggs, canola/olive oil blend, salt"),
        make_item("Chicken Apple Sausage", "chicken, apples, salt, sugar, dextrose, spices"),
        make_item("Breakfast Potatoes", "potatoes, onions, oil (canola oil, sunflower seed oil, cotton seed oil)"),
        make_item("Waffles", "enriched wheat flour, sugar, soybean oil, buckwheat flour"),
        make_item("Gluten-Free Waffles", "white rice flour, degerminated corn flour, cane sugar"),
    ]


def test_seasonal_fruit_counts_as_fruit():
    score, tags = score_item(make_item("Seasonal Fruit", "seasonal fresh fruit"))
    assert score > 0
    assert "+fruit" in tags


def test_any_sausage_is_processed_meat_even_with_apples():
    score, tags = score_item(make_item("Chicken Apple Sausage", "chicken, apples, salt, sugar"))
    assert score < 0
    assert "-ultra_processed_meat" in tags


def test_seed_oil_is_not_seeds():
    _, tags = score_item(make_item("Breakfast Potatoes", "potatoes, sunflower seed oil"))
    assert "+nuts_seeds" not in tags
    _, tags = score_item(make_item("Trail Mix", "sunflower seeds, raisins"))
    assert "+nuts_seeds" in tags


def test_oil_keywords_still_match_oil():
    _, tags = score_item(make_item("Roasted Squash", "squash, olive oil"))
    assert "+healthy_fats" in tags
    _, tags = score_item(make_item("Fish Oil Capsule Smoothie", "banana, fish oil"))
    assert "-fish_seafood" not in tags


def test_breakfast_plate_is_oatmeal_and_fruit_not_sausage():
    menu = DiningHallMenu(hall_name="Arrillaga", hall_id="a", date="2026-10-27",
                          meal="Breakfast", items=_breakfast_line())
    rec = build_deterministic_recommendation(score_all_halls([menu]), date="2026-10-27", meal="Breakfast")
    assert rec.recommended_plate[:2] == ["Steel Cut Oatmeal", "Seasonal Fruit"]
    assert "Chicken Apple Sausage" not in rec.recommended_plate
    assert "Breakfast Potatoes" not in rec.recommended_plate


def test_confidence_follows_the_plate_not_the_hall_total():
    # Waffles and eggs push the hall total negative, but the plate is solid.
    items = _breakfast_line() + [make_item("Greek Yogurt Parfait", "greek yogurt, berries, almonds")]
    menu = DiningHallMenu(hall_name="Arrillaga", hall_id="a", date="2026-10-27",
                          meal="Breakfast", items=items)
    scored = score_all_halls([menu])
    assert scored[0].total_score < 0
    rec = build_deterministic_recommendation(scored, date="2026-10-27", meal="Breakfast")
    assert rec.confidence == "medium"
