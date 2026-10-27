"""Where runs keep their JSON under data/ (gitignored).

The scheduled run writes these files and the Telegram bot reads them back, so
asking for a meal that was already sent is answered from disk: no scrape and
no OpenAI call.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .models import DiningHours, Recommendation, ScrapeResult

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def menus_path(data_dir: str, date_iso: str, meal: str) -> Path:
    return Path(data_dir) / "menus" / date_iso / f"{meal.lower()}.json"


def recommendation_path(data_dir: str, date_iso: str, meal: str) -> Path:
    return Path(data_dir) / "recommendations" / date_iso / f"{meal.lower()}.json"


def hours_path(data_dir: str, date_iso: str) -> Path:
    return Path(data_dir) / "hours" / f"{date_iso}.json"


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Saved: %s", path)


def _load(path: Path, model: Type[M]) -> Optional[M]:
    if not path.exists():
        return None
    try:
        return model(**json.loads(path.read_text()))
    except (ValueError, ValidationError) as exc:
        logger.warning("Ignoring unreadable %s (%s)", path, exc)
        return None


def load_scrape(data_dir: str, date_iso: str, meal: str) -> Optional[ScrapeResult]:
    return _load(menus_path(data_dir, date_iso, meal), ScrapeResult)


def load_recommendation(data_dir: str, date_iso: str, meal: str) -> Optional[Recommendation]:
    return _load(recommendation_path(data_dir, date_iso, meal), Recommendation)


def load_recent_hours(data_dir: str, today: date, max_age_days: int = 7) -> Optional[DiningHours]:
    """The newest saved hours no older than `max_age_days`.

    The hours page lists one weekday and one weekend schedule for the whole
    quarter, so any recent copy answers for any date.
    """
    oldest = (today - timedelta(days=max_age_days)).isoformat()
    for path in sorted((Path(data_dir) / "hours").glob("*.json"), reverse=True):
        if path.stem < oldest:
            break
        hours = _load(path, DiningHours)
        if hours is not None:
            return hours
    return None
