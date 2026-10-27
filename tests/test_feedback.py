import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from datetime import date

from src.feedback import (
    adjust_scores,
    hall_feedback_points,
    load_votes,
    prompt_feedback,
    record_vote,
    stats_html,
)
from src.models import Recommendation, ScoredHall
from src.storage import recommendation_path, save_json

CFG = {
    "feedback_points_per_vote": 1.5,
    "feedback_max_points": 4.5,
    "feedback_days": 30,
    "repeat_penalty": 3.0,
    "repeat_streak_days": 2,
}


def save_rec(data_dir, day, meal, best, plate=("Grilled Chicken",)):
    rec = Recommendation(best_hall=best, backup_hall="Other", recommended_plate=list(plate),
                         avoid=[], confidence="high", reasoning="", date=day, meal=meal)
    save_json(recommendation_path(str(data_dir), day, meal), rec.model_dump())


def hall(name, score):
    return ScoredHall(hall_name=name, hall_id=name, total_score=score, breakdown={}, item_count=5)


def test_vote_records_the_pick_it_was_about(tmp_path):
    save_rec(tmp_path, "2026-10-20", "Lunch", "Stern Dining", ["Tofu Bowl"])
    entry = record_vote(str(tmp_path), "2026-10-20", "Lunch", "down", "42")
    assert entry["best_hall"] == "Stern Dining"
    assert entry["plate"] == ["Tofu Bowl"]


def test_latest_vote_per_chat_and_meal_wins(tmp_path):
    for vote in ("up", "down", "up"):
        record_vote(str(tmp_path), "2026-10-20", "Lunch", vote, "42")
    record_vote(str(tmp_path), "2026-10-20", "Lunch", "down", "99")
    votes = load_votes(str(tmp_path))
    assert sorted((v["chat_id"], v["vote"]) for v in votes) == [("42", "up"), ("99", "down")]


def test_corrupt_lines_are_skipped(tmp_path):
    record_vote(str(tmp_path), "2026-10-20", "Lunch", "up", "42")
    with open(tmp_path / "feedback.jsonl", "a") as fh:
        fh.write("not json\n{}\n")
    assert len(load_votes(str(tmp_path))) == 1


def test_feedback_points_are_net_capped_and_windowed():
    today = date(2026, 10, 27)
    votes = [{"date": f"2026-10-{d}", "meal": "Lunch", "vote": "up", "best_hall": "A", "chat_id": "1"}
             for d in range(20, 27)]
    assert hall_feedback_points(votes[:2], "A", today, CFG) == 3.0
    assert hall_feedback_points(votes, "A", today, CFG) == 4.5          # capped
    assert hall_feedback_points(votes, "B", today, CFG) == 0
    old = [{"date": "2026-08-01", "meal": "Lunch", "vote": "down", "best_hall": "A", "chat_id": "1"}]
    assert hall_feedback_points(old, "A", today, CFG) == 0             # outside 30 days


def test_thumbs_up_can_flip_a_close_call(tmp_path):
    save_rec(tmp_path, "2026-10-21", "Dinner", "Stern Dining")
    record_vote(str(tmp_path), "2026-10-21", "Dinner", "up", "42")
    scored = adjust_scores([hall("Wilbur Dining", 20.0), hall("Stern Dining", 19.0)],
                           meal="Dinner", target_date=date(2026, 10, 27), data_dir=str(tmp_path))
    assert scored[0].hall_name == "Stern Dining"
    assert scored[0].breakdown["adjustments"]["feedback"] == 1.5


def test_repeat_penalty_after_two_days_as_best(tmp_path):
    save_rec(tmp_path, "2026-10-25", "Lunch", "Ricker Dining")
    save_rec(tmp_path, "2026-10-26", "Lunch", "Ricker Dining")
    scored = adjust_scores([hall("Ricker Dining", 30.0), hall("Lakeside Dining", 28.0)],
                           meal="Lunch", target_date=date(2026, 10, 27), data_dir=str(tmp_path))
    assert [s.hall_name for s in scored] == ["Lakeside Dining", "Ricker Dining"]
    assert scored[1].breakdown["adjustments"]["repeat"] == -3.0


def test_no_repeat_penalty_when_the_streak_is_broken(tmp_path):
    save_rec(tmp_path, "2026-10-25", "Lunch", "Ricker Dining")
    save_rec(tmp_path, "2026-10-26", "Lunch", "Stern Dining")
    scored = adjust_scores([hall("Ricker Dining", 30.0)], meal="Lunch",
                           target_date=date(2026, 10, 27), data_dir=str(tmp_path))
    assert scored[0].total_score == 30.0
    assert "adjustments" not in scored[0].breakdown


def test_prompt_feedback_and_stats(tmp_path):
    save_rec(tmp_path, "2026-10-26", "Lunch", "Stern Dining", ["Tofu Bowl"])
    record_vote(str(tmp_path), "2026-10-26", "Lunch", "down", "42")
    assert prompt_feedback(str(tmp_path)) == [
        {"date": "2026-10-26", "meal": "Lunch", "hall": "Stern Dining", "plate": ["Tofu Bowl"], "vote": "down"}
    ]
    text = stats_html(str(tmp_path), date(2026, 10, 27))
    assert "Stern Dining" in text and "1 👎" in text


def test_stats_with_no_votes(tmp_path):
    assert "No votes yet" in stats_html(str(tmp_path), date(2026, 10, 27))
