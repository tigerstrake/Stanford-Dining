from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, date, timezone
from typing import Optional, Tuple

import click
import pytz

from .feedback import adjust_scores, prompt_feedback
from .hours import load_dining_hours_safe
from .hours_watch import check_hours_change
from .models import DiningHours, Recommendation, ScrapeResult
from .notifier import notify, send_alert
from .recommender import get_recommendation
from .scorer import score_all_halls
from .scraper import StanfordMenuScraper
from .storage import hours_path, menus_path, recommendation_path, save_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

LA_TZ = pytz.timezone("America/Los_Angeles")

MEAL_TIME_RANGES = {
    # (start_hour, end_hour) — 24h
    "breakfast": (6, 10),
    "brunch": (9, 14),
    "lunch": (10, 15),
    "dinner": (15, 21),
}


# Meals to try, in order, when the detected meal has no menu at all. Only used
# for --meal auto; an explicitly requested meal is never swapped.
MEAL_FALLBACKS = {
    "Brunch": ["Lunch", "Breakfast"],
    "Lunch": ["Brunch"],
    "Breakfast": ["Brunch"],
    "Dinner": [],
}


def _detect_meal(now_la: datetime) -> str:
    hour = now_la.hour
    weekday = now_la.weekday()  # 0=Mon, 6=Sun
    is_weekend = weekday >= 5

    if is_weekend:
        # The hours page calls the weekend daytime sitting "Brunch/Lunch", but
        # the menu site publishes it under "Lunch" — its "Brunch" option is
        # empty on every date, which made every weekend run fail.
        return "Lunch" if hour < 15 else "Dinner"
    if 6 <= hour < 10:
        return "Breakfast"
    if 10 <= hour < 15:
        return "Lunch"
    if 15 <= hour < 21:
        return "Dinner"
    # Outside defined windows — pick nearest meal
    if hour < 6:
        return "Breakfast"
    return "Dinner"


def _resolve_meal(meal_arg: str, reference_dt: datetime) -> str:
    if meal_arg == "auto":
        return _detect_meal(reference_dt)
    meal_map = {
        "breakfast": "Breakfast",
        "brunch": "Brunch",
        "lunch": "Lunch",
        "dinner": "Dinner",
    }
    resolved = meal_map.get(meal_arg.lower())
    if not resolved:
        raise click.BadParameter(f"Unknown meal '{meal_arg}'. Use: auto, breakfast, lunch, brunch, dinner")
    return resolved


def _date_str_for_page(d: date) -> str:
    return f"{d.month}/{d.day}/{d.year}"


class ScrapeFailure(RuntimeError):
    """The menu site itself broke (layout changed, date missing), as opposed to
    menus simply not being posted yet."""


def scrape_menus(meal: str, target_date: date, fallback: bool, data_dir: str) -> Optional[ScrapeResult]:
    """Scrape every hall for one meal and save it; None if nothing is posted.

    With `fallback`, a meal with no items anywhere is retried as the nearest
    alternative (MEAL_FALLBACKS). Raises ScrapeFailure on structural failure.
    """
    now_utc = datetime.now(timezone.utc)
    date_str = _date_str_for_page(target_date)
    scraper = StanfordMenuScraper()
    candidates = [meal] + (MEAL_FALLBACKS.get(meal, []) if fallback else [])

    halls = []
    resolved_meal = meal
    for candidate in candidates:
        try:
            halls = scraper.scrape_all(date_str=date_str, meal=candidate)
        except RuntimeError as exc:
            raise ScrapeFailure(f"Scraping failed: {exc}") from exc
        if not halls:
            raise ScrapeFailure("No dining halls found")
        if any(h.items for h in halls):
            if candidate != meal:
                logger.warning(
                    "No %s menus for %s — using %s instead", meal, date_str, candidate
                )
                resolved_meal = candidate
            break
        logger.warning("All halls returned empty menus for %s / %s", date_str, candidate)

    halls_with_items = [h for h in halls if h.items]
    warnings = []
    if len(halls_with_items) == 0:
        # Not an error: menus are simply not posted (holiday, break, or not
        # yet published). Return cleanly so scheduled runs do not page anyone.
        logger.warning(
            "No menus posted for %s (%s) — nothing to recommend, skipping.",
            date_str, "/".join(candidates),
        )
        return None
    if len(halls_with_items) < 3:
        msg = (
            f"Only {len(halls_with_items)} hall(s) returned menu items — "
            "scrape may be unreliable."
        )
        logger.warning(msg)
        warnings.append(msg)

    scrape = ScrapeResult(
        date=target_date.isoformat(),
        meal=resolved_meal,
        halls=halls,
        scraped_at=now_utc.isoformat(),
        reliable=len(halls_with_items) >= 3,
        warnings=warnings,
    )
    save_json(menus_path(data_dir, scrape.date, resolved_meal), scrape.model_dump())
    return scrape


def run_pipeline(
    meal: str, target_date: date, *, fallback: bool, use_ai: bool, data_dir: str,
) -> Optional[Tuple[Recommendation, Optional[DiningHours]]]:
    """Scrape, score, recommend and fetch hours, saving each step under data_dir.

    Returns (recommendation, hours), or None when no menus are posted. Raises
    ScrapeFailure if the menu site is broken. Shared by the scheduled run and
    the Telegram bot.
    """
    scrape = scrape_menus(meal, target_date, fallback, data_dir)
    if scrape is None:
        return None

    # --- SCORE ---
    scored = score_all_halls(scrape.halls)
    scored = adjust_scores(scored, meal=scrape.meal, target_date=target_date, data_dir=data_dir)
    logger.info(
        "Scores: %s",
        ", ".join(f"{s.hall_name}={s.total_score:.1f}" for s in scored[:5]),
    )

    # --- RECOMMEND ---
    rec = get_recommendation(scrape, scored, use_ai=use_ai, feedback=prompt_feedback(data_dir))
    save_json(recommendation_path(data_dir, scrape.date, scrape.meal), rec.model_dump())

    # --- HOURS (best-effort; never blocks the message) ---
    hours = load_dining_hours_safe()
    if hours is not None:
        save_json(hours_path(data_dir, scrape.date), hours.model_dump())
    return rec, hours


@click.command()
@click.option("--meal", default="auto",
              help="Meal to fetch: auto, breakfast, lunch, brunch, dinner")
@click.option("--date", "date_arg", default=None, metavar="YYYY-MM-DD|today",
              help="Date to fetch (default: today in LA time)")
@click.option("--no-ai", is_flag=True, default=False,
              help="Skip OpenAI call, use deterministic scoring only")
@click.option("--dry-run", is_flag=True, default=False,
              help="Scrape and score but do not send Telegram notification")
@click.option("--data-dir", default="data", show_default=True,
              help="Root directory for saved JSON output")
@click.option("--verbose", "-v", is_flag=True, default=False)
def main(meal: str, date_arg: Optional[str], no_ai: bool, dry_run: bool,
         data_dir: str, verbose: bool) -> None:
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    now_la = datetime.now(timezone.utc).astimezone(LA_TZ)

    if date_arg and date_arg.lower() == "today":
        target_date = now_la.date()
    elif date_arg:
        try:
            target_date = datetime.strptime(date_arg, "%Y-%m-%d").date()
        except ValueError:
            raise click.BadParameter(
                f"Invalid date '{date_arg}'. Use YYYY-MM-DD or 'today'."
            )
    else:
        target_date = now_la.date()

    resolved_meal = _resolve_meal(meal, now_la)
    logger.info("Target: %s / %s / %s", target_date, resolved_meal, _date_str_for_page(target_date))

    try:
        result = run_pipeline(
            resolved_meal, target_date,
            fallback=(meal == "auto"), use_ai=not no_ai, data_dir=data_dir,
        )
    except ScrapeFailure as exc:
        logger.error("%s — failing loudly", exc)
        sys.exit(1)
    if result is None:
        return
    rec, hours = result

    # --- NOTIFY ---
    notify(rec, dry_run=dry_run, hours=hours)

    # --- HOURS WATCH (the send times in the timer follow the current hours) ---
    if hours is not None:
        alert = check_hours_change(hours, data_dir, save=not dry_run)
        if alert:
            if dry_run:
                logger.info("Dry-run: would send hours alert:\n%s", alert)
            else:
                send_alert(alert)


if __name__ == "__main__":
    main()
