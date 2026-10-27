import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from src.hours_watch import check_hours_change, earliest_openings, start_minutes, state_path
from src.models import DiningHours, HallHours

FALL = "Fall Hours Begin Friday, September 18th"


def make_hours(breakfast="7:30 a.m. - 10:00 a.m.", brunch="9:30 a.m. - 1:30 p.m.", note=FALL, halls=8):
    out = {}
    for i in range(halls):
        out[f"hall{i}"] = HallHours(
            hall_name=f"Hall {i}", note=note,
            weekday={"Breakfast": breakfast if i == 0 else "8:00 a.m. - 9:00 a.m.",
                     "Lunch": "11:00 a.m. - 1:30 p.m.", "Dinner": "5:00 p.m. - 8:00 p.m."},
            weekend={"Brunch/Lunch": brunch, "Dinner": "5:00 p.m. - 8:00 p.m."},
        )
    return DiningHours(fetched_at="2026-10-27T00:00:00Z", source_url="x", halls=out)


def test_start_minutes():
    assert start_minutes("7:30 a.m. - 10:00 a.m.") == 450
    assert start_minutes("5:00 p.m. - 8:00 p.m.") == 1020
    assert start_minutes("12:00 p.m. - 2:00 p.m.") == 720
    assert start_minutes("12:30 a.m. - 2:00 a.m.") == 30
    assert start_minutes("9 a.m. - 1 p.m.") == 540
    assert start_minutes("Closed") is None


def test_earliest_openings_take_the_earliest_hall():
    openings = earliest_openings(make_hours())
    assert openings["weekday"] == {"Breakfast": 450, "Lunch": 660, "Dinner": 1020}
    assert openings["weekend"] == {"Brunch/Lunch": 570, "Dinner": 1020}


def test_first_run_only_records_a_baseline(tmp_path):
    assert check_hours_change(make_hours(), str(tmp_path)) is None
    assert state_path(str(tmp_path)).exists()
    assert check_hours_change(make_hours(), str(tmp_path)) is None


def test_new_openings_alert_with_suggested_send_times(tmp_path):
    check_hours_change(make_hours(), str(tmp_path))
    alert = check_hours_change(
        make_hours(breakfast="8:00 a.m. - 10:00 a.m.", brunch="10:00 a.m. - 2:00 p.m.",
                   note="Winter Hours Begin Monday, January 5th"),
        str(tmp_path),
    )
    assert "Dining hours changed" in alert
    assert "Winter Hours" in alert
    assert "Mon–Fri: Breakfast 8:00 AM · Lunch 11:00 AM · Dinner 5:00 PM" in alert
    assert "Mon–Fri: 7:30 AM · 10:30 AM · 4:30 PM" in alert
    assert "Sat–Sun: 9:30 AM · 4:30 PM" in alert
    assert "stanford-dining.timer" in alert
    # Alerts once, then the new hours are the baseline.
    assert check_hours_change(
        make_hours(breakfast="8:00 a.m. - 10:00 a.m.", brunch="10:00 a.m. - 2:00 p.m.",
                   note="Winter Hours Begin Monday, January 5th"),
        str(tmp_path),
    ) is None


def test_note_change_alone_says_openings_unchanged(tmp_path):
    check_hours_change(make_hours(), str(tmp_path))
    alert = check_hours_change(make_hours(note="Fall Hours (updated)"), str(tmp_path))
    assert "(unchanged)" in alert
    assert "stanford-dining.timer" not in alert


def test_a_half_parsed_page_is_not_new_hours(tmp_path):
    check_hours_change(make_hours(), str(tmp_path))
    before = state_path(str(tmp_path)).read_text()
    assert check_hours_change(make_hours(halls=3), str(tmp_path)) is None
    assert state_path(str(tmp_path)).read_text() == before


def test_dry_run_leaves_the_baseline_for_the_real_run(tmp_path):
    check_hours_change(make_hours(), str(tmp_path))
    changed = make_hours(breakfast="8:00 a.m. - 10:00 a.m.")
    assert check_hours_change(changed, str(tmp_path), save=False) is not None
    assert check_hours_change(changed, str(tmp_path)) is not None
