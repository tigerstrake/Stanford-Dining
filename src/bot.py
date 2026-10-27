"""Telegram bot: answers messages and 👍/👎 taps (stanford-dining-bot.service).

It long-polls getUpdates, so it needs no public URL or open port. It only
answers chats listed in TELEGRAM_CHAT_ID: anyone can find a bot by its
username, and an answer can cost an OpenAI call.

It understands these as commands or plain text ("what's for dinner tomorrow",
"what's at flomo"):

  /now               the next meal
  /breakfast /lunch /dinner   add "tomorrow" for tomorrow
  /tomorrow          tomorrow's first meal
  /hall <name>       one hall's whole menu, each dish marked against your preferences
  /hours             every hall's hours today (or "tomorrow")
  /stats             your 👍/👎 so far

A meal that was already sent is answered from data/ (see storage.py), with no
scrape and no OpenAI call.

    python -m src.bot [--data-dir data]
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Set, Tuple

import click

from .feedback import record_vote, stats_html
from .hours import day_summary, hall_key, hours_for_meal, is_weekend, load_dining_hours_safe, lookup
from .main import LA_TZ, ScrapeFailure, _detect_meal, run_pipeline, scrape_menus
from .models import DiningHallMenu, DiningHours, HallHours
from .notifier import (
    allowed_chat_ids,
    call_api,
    feedback_keyboard,
    format_recommendation_html,
    parse_feedback_callback,
    send_message,
)
from .scorer import score_item
from .storage import load_recent_hours, load_recommendation, load_scrape

logger = logging.getLogger(__name__)

POLL_TIMEOUT_S = 50

COMMANDS = [
    {"command": "now", "description": "Pick for the next meal"},
    {"command": "breakfast", "description": "Today's breakfast (add: tomorrow)"},
    {"command": "lunch", "description": "Today's lunch / weekend brunch (add: tomorrow)"},
    {"command": "dinner", "description": "Today's dinner (add: tomorrow)"},
    {"command": "tomorrow", "description": "Tomorrow's first meal"},
    {"command": "hall", "description": "One hall's full menu, e.g. /hall arrillaga"},
    {"command": "hours", "description": "Every hall's hours today"},
    {"command": "stats", "description": "Your 👍/👎 so far"},
    {"command": "help", "description": "What I can do"},
]

HELP_HTML = """\
I send dining picks on a schedule, and you can ask me any time:

/now — the next meal
/breakfast · /lunch · /dinner — add "tomorrow" for tomorrow
/tomorrow — tomorrow's first meal
/hall arrillaga — one hall's full menu (or just "what's at flomo for dinner")
/hours — every hall's hours today
/stats — your 👍/👎 so far

Tap 👍 or 👎 under a pick and I'll lean that way next time."""

MEAL_WORDS = {
    "breakfast": "Breakfast",
    "brunch": "Lunch",          # the menu site files weekend brunch under Lunch
    "lunch": "Lunch",
    "dinner": "Dinner",
    "supper": "Dinner",
}

# What people call each hall -> a fragment of hours.hall_key(<menu hall name>).
HALL_ALIASES = {
    "arrillaga": "arrillaga",
    "arrillaga family": "arrillaga",
    "lakeside": "lakeside",
    "wilbur": "wilbur",
    "stern": "stern",
    "flomo": "florence moore",
    "florence moore": "florence moore",
    "florence": "florence moore",
    "ricker": "ricker",
    "casper": "gerhard casper",
    "gerhard casper": "gerhard casper",
    "gcd": "gerhard casper",
    "branner": "branner",
    "evgr": "evgr",
}


@dataclass
class Request:
    kind: str                       # "recommend" | "hall" | "hours" | "stats" | "help"
    day: date
    meal: Optional[str] = None      # None: pick by time of day
    hall: Optional[str] = None      # a HALL_ALIASES value, for kind == "hall"


# ------- understanding a message -------

def _find_meal(text: str) -> Optional[str]:
    for word, meal in MEAL_WORDS.items():
        if re.search(rf"\b{word}\b", text):
            return meal
    return None


def _find_hall(text: str) -> Optional[str]:
    # Longest alias first, so "florence moore" wins over "florence".
    for alias in sorted(HALL_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            return HALL_ALIASES[alias]
    return None


def parse_request(text: str, today: date) -> Request:
    raw = (text or "").strip().lower()
    command = None
    if raw.startswith("/"):
        head, _, raw = raw.partition(" ")
        command = head[1:].split("@", 1)[0]     # "/lunch@SDiningbot" in group chats

    day = today + timedelta(days=1) if re.search(r"\btomorrow\b", raw) else today
    meal = _find_meal(raw)
    hall = _find_hall(raw)

    if command in ("start", "help"):
        return Request("help", today)
    if command == "stats":
        return Request("stats", today)
    if command == "hours":
        return Request("hours", day)
    if command == "hall":
        return Request("hall", day, meal, hall) if hall else Request("help", today)
    if command == "now":
        return Request("recommend", today)
    if command == "tomorrow":
        return Request("recommend", today + timedelta(days=1), meal)
    if command in MEAL_WORDS:
        return Request("recommend", day, MEAL_WORDS[command])
    if command is not None:
        return Request("help", today)

    # Plain text.
    if re.search(r"\b(stats|votes)\b", raw):
        return Request("stats", today)
    if re.search(r"\b(hours|open|opens|close|closes)\b", raw):
        return Request("hours", day)
    if hall:
        return Request("hall", day, meal, hall)
    if meal or re.search(r"\b(now|eat|food|hungry|menu|recommend|pick|today|tomorrow|tonight)\b", raw):
        return Request("recommend", day, meal)
    return Request("help", today)


def first_meal(day: date) -> str:
    return "Lunch" if is_weekend(day) else "Breakfast"


def resolve_meal(req: Request, now_la: datetime) -> Tuple[str, date, bool]:
    """(meal, day, fallback) to fetch. fallback mirrors `--meal auto`."""
    if req.meal is None:
        if req.day != now_la.date():
            return first_meal(req.day), req.day, True
        if now_la.hour >= 21:       # dinner is over everywhere; answer for tomorrow
            tomorrow = req.day + timedelta(days=1)
            return first_meal(tomorrow), tomorrow, True
        return _detect_meal(now_la), req.day, True
    if req.meal == "Breakfast" and is_weekend(req.day):
        return "Lunch", req.day, False      # weekends only have brunch, filed under Lunch
    return req.meal, req.day, False


# ------- answering -------

def _hours(data_dir: str, today: date) -> Optional[DiningHours]:
    return load_recent_hours(data_dir, today) or load_dining_hours_safe()


def _no_menus(meal: str, day: date) -> str:
    return f"No {meal.lower()} menus posted for {day:%A %b %-d} yet."


def answer_recommend(meal: str, day: date, fallback: bool, data_dir: str,
                     today: date) -> Tuple[str, Optional[dict]]:
    rec = load_recommendation(data_dir, day.isoformat(), meal)
    hours = None
    if rec is None:
        try:
            result = run_pipeline(meal, day, fallback=fallback, use_ai=True, data_dir=data_dir)
        except ScrapeFailure as exc:
            logger.warning("On-demand scrape failed: %s", exc)
            return f"Couldn't get {day:%A}'s menus from the dining site: {html.escape(str(exc))}", None
        if result is None:
            return _no_menus(meal, day), None
        rec, hours = result
    if rec.best_hall == "None":
        return _no_menus(meal, day), None
    hours = hours or _hours(data_dir, today)
    return format_recommendation_html(rec, hours), feedback_keyboard(rec.date, rec.meal)


def format_hall_menu(menu: DiningHallMenu, meal: str, day: date, hours: Optional[DiningHours]) -> str:
    hall_hours = lookup(hours, menu.hall_name)
    window = hours_for_meal(hall_hours, meal, is_weekend(day)) if hall_hours else None
    title = f"🍽 <b>{html.escape(menu.hall_name)} — {html.escape(meal)}, {day:%a %b %-d}</b>"
    lines = [title + (f"\n🕒 {html.escape(window)}" if window else ""), ""]
    scored = sorted(((score_item(it)[0], it.name) for it in menu.items), key=lambda x: -x[0])
    for score, name in scored:
        mark = "✅" if score > 0 else ("⛔" if score < 0 else "▫️")
        lines.append(f"{mark} {html.escape(name)}")
    lines += ["", "<i>✅ fits your preferences · ⛔ skip</i>"]
    return "\n".join(lines)


def answer_hall(req: Request, now_la: datetime, data_dir: str) -> str:
    meal, day, fallback = resolve_meal(req, now_la)
    scrape = load_scrape(data_dir, day.isoformat(), meal)
    if scrape is None:
        try:
            scrape = scrape_menus(meal, day, fallback, data_dir)
        except ScrapeFailure as exc:
            logger.warning("On-demand scrape failed: %s", exc)
            return f"Couldn't get {day:%A}'s menus from the dining site: {html.escape(str(exc))}"
        if scrape is None:
            return _no_menus(meal, day)
    menu = next((h for h in scrape.halls if req.hall in hall_key(h.hall_name)), None)
    if menu is None:
        return f"I don't see that hall on the {scrape.meal.lower()} menu for {day:%A}."
    if not menu.items:
        return f"{html.escape(menu.hall_name)} isn't serving {scrape.meal.lower()} on {day:%A %b %-d}."
    return format_hall_menu(menu, scrape.meal, day, _hours(data_dir, now_la.date()))


def answer_hours(day: date, data_dir: str, today: date) -> str:
    hours = _hours(data_dir, today)
    if hours is None:
        return "Couldn't load the dining hours page right now."
    lines = [f"🕒 <b>Hours {day:%A %b %-d}</b>", ""]
    notes = set()
    for raw in hours.halls.values():
        hall = raw if isinstance(raw, HallHours) else HallHours(**raw)
        summary = day_summary(hall, is_weekend(day))
        lines.append(f"<b>{html.escape(hall.hall_name)}:</b> {html.escape(summary or 'closed')}")
        if hall.note:
            notes.add(hall.note)
    if notes:
        lines += ["", f"<i>{html.escape('; '.join(sorted(notes)))}</i>"]
    return "\n".join(lines)


def answer(req: Request, now_la: datetime, data_dir: str) -> Tuple[str, Optional[dict]]:
    """(HTML text, reply_markup) for one request."""
    today = now_la.date()
    if req.kind == "recommend":
        meal, day, fallback = resolve_meal(req, now_la)
        return answer_recommend(meal, day, fallback, data_dir, today)
    if req.kind == "hall":
        return answer_hall(req, now_la, data_dir), None
    if req.kind == "hours":
        return answer_hours(req.day, data_dir, today), None
    if req.kind == "stats":
        return stats_html(data_dir, today), None
    return HELP_HTML, None


# ------- Telegram plumbing -------

def handle_callback(cq: dict, allowed: Set[str], data_dir: str) -> None:
    message = cq.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if chat_id not in allowed:
        logger.warning("Ignoring button tap from chat %s", chat_id)
        return
    parsed = parse_feedback_callback(cq.get("data", ""))
    if parsed is None:
        call_api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        return
    date_iso, meal, vote = parsed
    record_vote(data_dir, date_iso, meal, vote, chat_id)
    call_api("answerCallbackQuery", {
        "callback_query_id": cq["id"],
        "text": "Noted 👍 — more like this" if vote == "up" else "Noted 👎 — I'll steer away",
    })
    call_api("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message.get("message_id"),
        "reply_markup": feedback_keyboard(date_iso, meal, selected=vote),
    })


def handle_update(update: dict, allowed: Set[str], data_dir: str,
                  now_la: Optional[datetime] = None) -> None:
    if "callback_query" in update:
        handle_callback(update["callback_query"], allowed, data_dir)
        return
    message = update.get("message") or {}
    text = message.get("text")
    chat_id = str((message.get("chat") or {}).get("id", ""))
    if not text:
        return
    if chat_id not in allowed:
        logger.warning("Ignoring message from chat %s", chat_id)
        return
    now_la = now_la or datetime.now(timezone.utc).astimezone(LA_TZ)
    req = parse_request(text, now_la.date())
    logger.info("Chat %s asked %r -> %s", chat_id, text[:80], req)
    if req.kind in ("recommend", "hall"):
        call_api("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    reply, markup = answer(req, now_la, data_dir)
    send_message(chat_id, reply, reply_markup=markup)


def _load_offset(path: Path) -> Optional[int]:
    try:
        return int(json.loads(path.read_text())["offset"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _save_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}))


def poll_forever(data_dir: str) -> None:
    allowed = set(allowed_chat_ids())
    state = Path(data_dir) / "bot_state.json"
    offset = _load_offset(state)
    call_api("setMyCommands", {"commands": COMMANDS})
    logger.info("Listening for messages from %d chat(s)", len(allowed))
    backoff = 5
    while True:
        params = {"timeout": POLL_TIMEOUT_S, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        updates = call_api("getUpdates", params, timeout=POLL_TIMEOUT_S + 15)
        if updates is None:         # already logged; Telegram or the network is down
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = 5
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(update, allowed, data_dir)
            except Exception:
                logger.exception("Failed to handle update %s", update.get("update_id"))
            # Saved per update, so a restart neither replays nor drops messages.
            _save_offset(state, offset)


@click.command()
@click.option("--data-dir", default="data", show_default=True)
def main(data_dir: str) -> None:
    if not allowed_chat_ids():
        logger.error("TELEGRAM_CHAT_ID is not set; refusing to answer anyone")
        sys.exit(1)
    poll_forever(data_dir)


if __name__ == "__main__":
    main()
