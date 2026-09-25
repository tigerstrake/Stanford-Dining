from __future__ import annotations

import html
import logging
import os
from datetime import date
from typing import List, Optional

import requests

from .hours import day_summary, hours_for_meal, is_weekend, lookup
from .models import DiningHours, Recommendation

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"

# Telegram caps a single sendMessage at 4096 characters (after entity parsing).
TELEGRAM_MAX_MESSAGE_CHARS = 4096


def _esc(text: str) -> str:
    """Escape user/scraped text for Telegram's HTML parse mode.

    Dish names come from a third-party web page, so anything that looks like a
    tag or entity must be neutralised or Telegram rejects the whole message.
    """
    return html.escape(str(text), quote=False)


def _rec_date(rec: Recommendation) -> Optional[date]:
    try:
        return date.fromisoformat(rec.date)
    except ValueError:
        return None


def _meal_window(rec: Recommendation, hours: Optional[DiningHours], hall_name: str) -> str:
    """" (Lunch 11:00 a.m. - 1:30 p.m.)" for the recommended meal, or "" if unknown."""
    d = _rec_date(rec)
    hall = lookup(hours, hall_name)
    if d is None or hall is None:
        return ""
    window = hours_for_meal(hall, rec.meal, is_weekend(d))
    return f" ({rec.meal.title()} {window})" if window else ""


def _hours_lines(rec: Recommendation, hours: Optional[DiningHours]) -> List[tuple]:
    """[(hall_name, day summary)] for the best and backup halls, in that order."""
    d = _rec_date(rec)
    if d is None or hours is None:
        return []
    out = []
    for name in (rec.best_hall, rec.backup_hall):
        hall = lookup(hours, name)
        if hall is None or name in [n for n, _ in out]:
            continue
        summary = day_summary(hall, is_weekend(d))
        if summary:
            out.append((name, summary))
    return out


def _hours_note(rec: Recommendation, hours: Optional[DiningHours]) -> str:
    for name in (rec.best_hall, rec.backup_hall):
        hall = lookup(hours, name)
        if hall is not None and hall.note:
            return hall.note
    return ""


def _day_name(rec: Recommendation) -> str:
    d = _rec_date(rec)
    return d.strftime("%A") if d else "today"


def format_recommendation_html(rec: Recommendation, hours: Optional[DiningHours] = None) -> str:
    """Telegram HTML-formatted message (parse_mode=HTML)."""
    lines = [
        f"🍽 <b>Stanford Dining — {_esc(rec.meal.title())} on {_esc(rec.date)}</b>",
        "",
        f"<b>Best hall:</b> {_esc(rec.best_hall)}{_esc(_meal_window(rec, hours, rec.best_hall))}",
        f"<b>Backup hall:</b> {_esc(rec.backup_hall)}{_esc(_meal_window(rec, hours, rec.backup_hall))}",
        f"<b>Confidence:</b> {_esc(rec.confidence.upper())}",
        "",
        "<b>Recommended plate</b>",
    ]
    for item in rec.recommended_plate:
        lines.append(f"  • {_esc(item)}")
    if rec.avoid:
        lines.append("")
        lines.append("<b>Skip</b>")
        for item in rec.avoid:
            lines.append(f"  • {_esc(item)}")
    lines.append("")
    lines.append(f"<b>Reasoning:</b> {_esc(rec.reasoning)}")
    if not rec.ai_generated:
        lines.append("<i>(deterministic scoring — AI not used)</i>")

    hours_lines = _hours_lines(rec, hours)
    if hours_lines:
        lines.append("")
        lines.append(f"🕒 <b>Hours {_esc(_day_name(rec))}</b>")
        for name, summary in hours_lines:
            lines.append(f"<b>{_esc(name)}:</b> {_esc(summary)}")
        note = _hours_note(rec, hours)
        if note:
            lines.append(f"<i>{_esc(note)}</i>")
    return "\n".join(lines)


def format_recommendation_plain(rec: Recommendation, hours: Optional[DiningHours] = None) -> str:
    lines = [
        f"Stanford Dining Recommendation — {rec.meal.title()} on {rec.date}",
        "=" * 60,
        f"Best hall:    {rec.best_hall}{_meal_window(rec, hours, rec.best_hall)}",
        f"Backup hall:  {rec.backup_hall}{_meal_window(rec, hours, rec.backup_hall)}",
        f"Confidence:   {rec.confidence.upper()}",
        "",
        "Recommended plate:",
    ]
    for item in rec.recommended_plate:
        lines.append(f"  - {item}")
    if rec.avoid:
        lines.append("")
        lines.append("Skip:")
        for item in rec.avoid:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append(f"Reasoning: {rec.reasoning}")
    if not rec.ai_generated:
        lines.append("(deterministic scoring — AI not used)")

    hours_lines = _hours_lines(rec, hours)
    if hours_lines:
        lines.append("")
        lines.append(f"Hours {_day_name(rec)}:")
        for name, summary in hours_lines:
            lines.append(f"  {name}: {summary}")
        note = _hours_note(rec, hours)
        if note:
            lines.append(f"  ({note})")
    return "\n".join(lines)


def _truncate(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> str:
    if len(text) <= limit:
        return text
    # Cut on a line boundary so we never split an HTML tag in half.
    cut = text[: limit - 1].rsplit("\n", 1)[0]
    return cut + "…"


def _parse_chat_ids(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


def send_telegram(
    rec: Recommendation,
    bot_token: Optional[str] = None,
    chat_ids: Optional[List[str]] = None,
    api_base: str = TELEGRAM_API_BASE,
    timeout: int = 15,
    hours: Optional[DiningHours] = None,
) -> bool:
    """Send the recommendation to one or more Telegram chats via the Bot API.

    Returns True if at least one chat received the message. Never raises: a
    notification failure must not fail the run, the plain-text version has
    already gone to stdout by the time this is called.
    """
    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    targets = chat_ids if chat_ids is not None else _parse_chat_ids(os.environ.get("TELEGRAM_CHAT_ID"))

    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram notification")
        return False
    if not targets:
        logger.warning("TELEGRAM_CHAT_ID not set — skipping Telegram notification")
        return False

    text = _truncate(format_recommendation_html(rec, hours))
    url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"

    delivered = 0
    for chat_id in targets:
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=timeout,
            )
            body = {}
            try:
                body = resp.json()
            except ValueError:
                pass
            # Telegram answers application errors (bad chat id, bad HTML) with
            # HTTP 400 *and* {"ok": false, "description": ...}; check both.
            if resp.status_code == 200 and body.get("ok"):
                delivered += 1
                logger.info("Telegram notification sent to chat %s", chat_id)
            else:
                logger.error(
                    "Telegram rejected message for chat %s: HTTP %s — %s",
                    chat_id, resp.status_code, body.get("description", resp.text[:200]),
                )
        except Exception as exc:
            # Never log the URL: it contains the bot token.
            logger.error("Telegram notification failed for chat %s: %s", chat_id, exc)

    return delivered > 0


def notify(rec: Recommendation, dry_run: bool = False, hours: Optional[DiningHours] = None) -> None:
    plain = format_recommendation_plain(rec, hours)
    print(plain)

    if dry_run:
        logger.info("Dry-run mode: skipping Telegram notification")
        return

    success = send_telegram(rec, hours=hours)
    if not success:
        logger.info("Recommendation printed to stdout as fallback")
