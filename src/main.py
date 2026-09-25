from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import click
import pytz

from .models import ScrapeResult
from .notifier import notify
from .recommender import get_recommendation
from .scorer import score_all_halls
from .scraper import StanfordMenuScraper

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


def _detect_meal(now_la: datetime) -> str:
    hour = now_la.hour
    weekday = now_la.weekday()  # 0=Mon, 6=Sun
    is_weekend = weekday >= 5

    if 6 <= hour < 10:
        return "Brunch" if is_weekend else "Breakfast"
    if 10 <= hour < 15:
        return "Brunch" if is_weekend else "Lunch"
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


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Saved: %s", path)


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

    now_utc = datetime.now(timezone.utc)
    now_la = now_utc.astimezone(LA_TZ)

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
    date_str = _date_str_for_page(target_date)
    date_iso = target_date.isoformat()

    logger.info("Target: %s / %s / %s", target_date, resolved_meal, date_str)

    # --- SCRAPE ---
    scraper = StanfordMenuScraper()
    try:
        halls = scraper.scrape_all(date_str=date_str, meal=resolved_meal)
    except RuntimeError as exc:
        logger.error("Scraping failed: %s", exc)
        sys.exit(1)

    if not halls:
        logger.error("No dining halls found — failing loudly")
        sys.exit(1)

    halls_with_items = [h for h in halls if h.items]
    warnings = []
    if len(halls_with_items) == 0:
        logger.error(
            "All halls returned empty menus for %s / %s. "
            "The menu may not be posted yet.",
            date_str, resolved_meal,
        )
        sys.exit(1)
    if len(halls_with_items) < 3:
        msg = (
            f"Only {len(halls_with_items)} hall(s) returned menu items — "
            "scrape may be unreliable."
        )
        logger.warning(msg)
        warnings.append(msg)

    scrape = ScrapeResult(
        date=date_iso,
        meal=resolved_meal,
        halls=halls,
        scraped_at=now_utc.isoformat(),
        reliable=len(halls_with_items) >= 3,
        warnings=warnings,
    )

    # Save raw scrape
    menu_path = Path(data_dir) / "menus" / date_iso / f"{resolved_meal.lower()}.json"
    _save_json(menu_path, scrape.model_dump())

    # --- SCORE ---
    scored = score_all_halls(halls)
    logger.info(
        "Scores: %s",
        ", ".join(f"{s.hall_name}={s.total_score:.1f}" for s in scored[:5]),
    )

    # --- RECOMMEND ---
    rec = get_recommendation(scrape, scored, use_ai=not no_ai)

    # Save recommendation
    rec_path = Path(data_dir) / "recommendations" / date_iso / f"{resolved_meal.lower()}.json"
    _save_json(rec_path, rec.model_dump())

    # --- NOTIFY ---
    notify(rec, dry_run=dry_run)


if __name__ == "__main__":
    main()
