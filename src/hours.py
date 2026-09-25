"""Dining hall opening hours from the R&DE "Dining Locations & Hours" page.

The page lists each hall under an <h2> with plain-text lines like
"Monday - Friday" / "Breakfast: 7:30 a.m. - 10:00 a.m.". There is no API, so
we parse the visible text. Anything that fails here is logged and the
recommendation goes out without hours — hours must never block a message.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .models import DiningHours, HallHours

logger = logging.getLogger(__name__)

HOURS_URL = "https://rde.stanford.edu/dining-hospitality/dining-locations-hours"

_DAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_DAY_RANGE_RE = re.compile(rf"^({_DAY})\s*[-–]\s*({_DAY})$", re.I)
_TIME = r"\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?"
_TIME_RANGE_RE = re.compile(rf"^{_TIME}\s*[-–]\s*{_TIME}$", re.I)
_MEAL_RE = re.compile(rf"^(Breakfast|Brunch/Lunch|Brunch|Lunch|Dinner|Late Night)\s*:\s*({_TIME}\s*[-–]\s*{_TIME})$", re.I)
_NOTE_RE = re.compile(r"\bhours\b", re.I)
_ADDRESS_RE = re.compile(r"Stanford,\s*CA", re.I)

# Words that differ between the menu page's hall names and the hours page's
# ("Gerhard Casper Dining" vs "Gerhard Casper Dining Commons").
_NOISE_WORDS = {"dining", "commons", "family", "hall", "the"}


def hall_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in _NOISE_WORDS)


def _hall_cards(soup: BeautifulSoup) -> List[tuple]:
    """[(hall name, container element)] for every dining-location card on the page.

    The page mixes two card styles: some name the hall in a separate title
    <div> next to the body, others put an <h2> inside the body. Fall back to
    every <h2>/<h3> on the page if the card classes ever change; halls with
    no parseable hours are dropped later anyway.
    """
    cards: List[tuple] = []
    for body in soup.select("div.field--name-field-titbab-body"):
        title = body.find_previous_sibling(class_="field--name-field-titbab-title")
        heading = body.find(["h2", "h3"])
        name = (title.get_text(strip=True) if title else "") or \
               (heading.get_text(strip=True) if heading else "")
        if name:
            cards.append((name, body))
    if cards:
        return cards
    for h in soup.find_all(["h2", "h3"]):
        name = h.get_text(strip=True)
        if name and h.parent is not None:
            cards.append((name, h.parent))
    return cards


def parse_hours_html(html: str) -> Dict[str, HallHours]:
    soup = BeautifulSoup(html, "lxml")
    cards = _hall_cards(soup)
    all_names = {name for name, _ in cards}

    halls: Dict[str, HallHours] = {}
    for name, container in cards:
        lines = [l.strip() for l in container.get_text("\n").splitlines() if l.strip()]
        # The container should hold exactly one hall; stop if another heading appears.
        body: List[str] = []
        for line in lines[1:] if lines and lines[0] == name else lines:
            if line != name and line in all_names:
                break
            body.append(line)

        hall = HallHours(hall_name=name)
        block: Optional[str] = None      # "weekday" | "weekend" | "other"
        current_range = ""
        prev_line = ""
        for line in body:
            m = _DAY_RANGE_RE.match(line)
            if m:
                start, end = m.group(1).title(), m.group(2).title()
                if start == "Monday" and end == "Friday":
                    block = "weekday"
                elif start == "Saturday" and end == "Sunday":
                    block = "weekend"
                else:
                    block = "other"
                current_range = f"{start} - {end}"
                # A named late-night section ("Arrillaga Nights") precedes its day range.
                if block == "other" and prev_line and not _NOTE_RE.search(prev_line) \
                        and not _MEAL_RE.match(prev_line) and not _TIME_RANGE_RE.match(prev_line):
                    hall.late_night = prev_line
                elif block == "other" and prev_line and "late night" in prev_line.lower():
                    hall.late_night = prev_line
                prev_line = line
                continue

            m = _MEAL_RE.match(line)
            if m and block in ("weekday", "weekend"):
                target = hall.weekday if block == "weekday" else hall.weekend
                target[m.group(1).title().replace("/lunch", "/Lunch")] = _tidy(m.group(2))
                prev_line = line
                continue

            if _TIME_RANGE_RE.match(line) and block == "other":
                label = hall.late_night or "Late night"
                hall.late_night = f"{label} ({current_range}): {_tidy(line)}"
                prev_line = line
                continue

            if _NOTE_RE.search(line) and not _ADDRESS_RE.search(line) and not hall.note \
                    and "late night" not in line.lower():
                hall.note = line.rstrip("  ")
            prev_line = line

        if hall.weekday or hall.weekend or hall.late_night:
            halls[hall_key(name)] = hall

    return halls


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("–", "-")).strip()


def fetch_dining_hours(timeout: int = 20) -> DiningHours:
    resp = requests.get(
        HOURS_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; stanford-dining-recommender)"},
        timeout=timeout,
    )
    resp.raise_for_status()
    halls = parse_hours_html(resp.text)
    if not halls:
        raise RuntimeError("No dining hall hours found on page — site structure may have changed")
    return DiningHours(
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url=HOURS_URL,
        halls=halls,
    )


def load_dining_hours_safe(timeout: int = 20) -> Optional[DiningHours]:
    """Fetch hours, returning None (and logging) on any failure."""
    try:
        hours = fetch_dining_hours(timeout=timeout)
        logger.info("Fetched hours for %d dining halls", len(hours.halls))
        return hours
    except Exception as exc:
        logger.warning("Could not fetch dining hours (%s) — message will omit them", exc)
        return None


def lookup(hours: Optional[DiningHours], hall_name: str) -> Optional[HallHours]:
    if hours is None:
        return None
    key = hall_key(hall_name)
    hall = hours.halls.get(key)
    if hall is None:
        return None
    return hall if isinstance(hall, HallHours) else HallHours(**hall)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def hours_for_meal(hall: HallHours, meal: str, weekend: bool) -> Optional[str]:
    """The time window for one meal, e.g. "11:00 a.m. - 1:30 p.m." (None if not served)."""
    block = hall.weekend if weekend else hall.weekday
    meal_l = meal.lower()
    for label, window in block.items():
        if label.lower() == meal_l:
            return window
    for label, window in block.items():
        # "Brunch/Lunch" serves both brunch and lunch.
        if meal_l in [part.strip() for part in label.lower().split("/")]:
            return window
    return None


def day_summary(hall: HallHours, weekend: bool) -> str:
    """All meals for the day, e.g. "Breakfast 7:30 a.m. - 9:00 a.m. · Lunch ... · Dinner ..."."""
    block = hall.weekend if weekend else hall.weekday
    parts = [f"{label} {window}" for label, window in block.items()]
    if hall.late_night and ":" in hall.late_night:
        parts.append(hall.late_night)
    return " · ".join(parts)
