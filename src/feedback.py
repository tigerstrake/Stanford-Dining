"""👍/👎 votes on recommendations, and the score nudges they cause.

Votes come in through the Telegram bot (src/bot.py) and are appended to
data/feedback.jsonl, one JSON object per line. The latest vote per chat for a
given date + meal wins, so changing your mind just appends another line.

Two nudges are applied to hall scores before the AI sees them (and to the
scoring-only fallback). Both are tuned under `adjustments:` in
config/preferences.yml:

- feedback: every net 👍 (or 👎) on recent picks where a hall was the best
  hall adds (or removes) points for that hall, up to a cap.
- repeat: a hall that was already the best pick for this meal on each of the
  last few days loses points, so near-ties rotate instead of repeating.

The AI also gets the recent votes themselves, with the plate, so it can lean
toward specific dishes and away from others (see recommender.py).
"""

from __future__ import annotations

import html
import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import ScoredHall
from .preferences import load as load_prefs
from .storage import load_recommendation

logger = logging.getLogger(__name__)

DEFAULTS = {
    "feedback_points_per_vote": 1.5,
    "feedback_max_points": 4.5,
    "feedback_days": 30,
    "repeat_penalty": 3.0,
    "repeat_streak_days": 2,
}

VOTE_VALUE = {"up": 1, "down": -1}


def settings() -> Dict[str, float]:
    return {**DEFAULTS, **(load_prefs().get("adjustments") or {})}


def feedback_path(data_dir: str) -> Path:
    return Path(data_dir) / "feedback.jsonl"


def record_vote(data_dir: str, date_iso: str, meal: str, vote: str, chat_id: str,
                now: Optional[datetime] = None) -> dict:
    """Append one vote, together with the pick it was about."""
    if vote not in VOTE_VALUE:
        raise ValueError(f"Unknown vote {vote!r}")
    rec = load_recommendation(data_dir, date_iso, meal)
    entry = {
        "voted_at": (now or datetime.now(timezone.utc)).isoformat(),
        "date": date_iso,
        "meal": meal,
        "vote": vote,
        "chat_id": str(chat_id),
        "best_hall": rec.best_hall if rec else None,
        "plate": rec.recommended_plate if rec else [],
    }
    path = feedback_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.info("Recorded %s for %s %s (%s)", vote, date_iso, meal, entry["best_hall"])
    return entry


def load_votes(data_dir: str) -> List[dict]:
    """Current votes, oldest first: the last line per (chat, date, meal) wins."""
    path = feedback_path(data_dir)
    if not path.exists():
        return []
    latest: Dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            v = json.loads(line)
            key = (v["chat_id"], v["date"], v["meal"])
        except (ValueError, KeyError, TypeError):
            continue
        latest.pop(key, None)        # re-insert so dict order follows the latest vote
        latest[key] = v
    return list(latest.values())


def _votes_since(votes: List[dict], first_day: date, last_day: date) -> List[dict]:
    out = []
    for v in votes:
        try:
            d = date.fromisoformat(v["date"])
        except (ValueError, KeyError, TypeError):
            continue
        if first_day <= d <= last_day:
            out.append(v)
    return out


def hall_feedback_points(votes: List[dict], hall_name: str, today: date,
                         cfg: Optional[Dict[str, float]] = None) -> float:
    cfg = cfg or settings()
    window = _votes_since(votes, today - timedelta(days=int(cfg["feedback_days"])), today)
    net = sum(VOTE_VALUE.get(v.get("vote"), 0) for v in window if v.get("best_hall") == hall_name)
    cap = float(cfg["feedback_max_points"])
    return max(-cap, min(cap, net * float(cfg["feedback_points_per_vote"])))


def on_repeat_streak(data_dir: str, hall_name: str, meal: str, target_date: date, days: int) -> bool:
    """True if `hall_name` was the best pick for `meal` on each of the previous `days` days."""
    if days <= 0:
        return False
    for back in range(1, days + 1):
        rec = load_recommendation(data_dir, (target_date - timedelta(days=back)).isoformat(), meal)
        if rec is None or rec.best_hall != hall_name:
            return False
    return True


def adjust_scores(scored: List[ScoredHall], meal: str, target_date: date, data_dir: str) -> List[ScoredHall]:
    """Apply the feedback and repeat nudges, recording them in each hall's breakdown."""
    cfg = settings()
    votes = load_votes(data_dir)
    for hall in scored:
        if hall.item_count == 0:
            continue
        fb = hall_feedback_points(votes, hall.hall_name, target_date, cfg)
        repeat = 0.0
        if cfg["repeat_penalty"] and on_repeat_streak(
            data_dir, hall.hall_name, meal, target_date, int(cfg["repeat_streak_days"])
        ):
            repeat = -float(cfg["repeat_penalty"])
        if fb or repeat:
            hall.total_score = round(hall.total_score + fb + repeat, 2)
            hall.breakdown["adjustments"] = {"feedback": fb, "repeat": repeat}
            logger.info("Adjusted %s: feedback %+.1f, repeat %+.1f", hall.hall_name, fb, repeat)
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored


def prompt_feedback(data_dir: str, limit: int = 12) -> List[dict]:
    """The most recent votes, trimmed to what the AI needs."""
    return [
        {"date": v["date"], "meal": v["meal"], "hall": v.get("best_hall"),
         "plate": v.get("plate", []), "vote": v["vote"]}
        for v in load_votes(data_dir)[-limit:]
    ]


def stats_html(data_dir: str, today: date, days: int = 30) -> str:
    window = _votes_since(load_votes(data_dir), today - timedelta(days=days), today)
    if not window:
        return "No votes yet. Tap 👍 or 👎 under a recommendation and I'll lean that way next time."
    per_hall: Dict[str, List[int]] = {}
    for v in window:
        counts = per_hall.setdefault(v.get("best_hall") or "?", [0, 0])
        counts[0 if v.get("vote") == "up" else 1] += 1
    cfg = settings()
    lines = [f"📊 <b>Your votes, last {days} days</b>", ""]
    for hall, (up, down) in sorted(per_hall.items(), key=lambda kv: kv[1][1] - kv[1][0]):
        points = hall_feedback_points(window, hall, today, cfg)
        lines.append(f"<b>{html.escape(hall)}</b>: {up} 👍 · {down} 👎 ({points:+.1f} pts)")
    return "\n".join(lines)
