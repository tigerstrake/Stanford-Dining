from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup, Tag

from .models import DiningHallMenu, MenuItem

logger = logging.getLogger(__name__)

MENU_URL = "https://rdeapps.stanford.edu/dininghallmenu/Menu.aspx"

# Known dining hall IDs from the page's location dropdown
KNOWN_HALLS: Dict[str, str] = {
    "Arrillaga": "Arrillaga Family Dining Commons",
    "Branner": "Branner Dining",
    "EVGR": "EVGR Dining",
    "FlorenceMoore": "Florence Moore Dining",
    "GerhardCasper": "Gerhard Casper Dining",
    "Lakeside": "Lakeside Dining",
    "Ricker": "Ricker Dining",
    "Stern": "Stern Dining",
    "Wilbur": "Wilbur Dining",
}


def _make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def _extract_hidden_fields(soup: BeautifulSoup) -> Dict[str, str]:
    fields = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
                 "__EVENTTARGET", "__EVENTARGUMENT"):
        tag = soup.find("input", {"name": name})
        if tag:
            fields[name] = tag.get("value", "")
    return fields


def _extract_locations(soup: BeautifulSoup) -> List[Tuple[str, str]]:
    select = soup.find("select", {"id": "MainContent_lstLocations"})
    if not select:
        return []
    return [
        (opt["value"], opt.get_text(strip=True))
        for opt in select.find_all("option")
        if opt.get("value", "").strip()
    ]


def _extract_day_options(soup: BeautifulSoup) -> List[str]:
    select = soup.find("select", {"id": "MainContent_lstDay"})
    if not select:
        return []
    return [opt["value"] for opt in select.find_all("option") if opt.get("value", "").strip()]


def _extract_meal_options(soup: BeautifulSoup) -> List[str]:
    select = soup.find("select", {"id": "MainContent_lstMealType"})
    if not select:
        return []
    return [opt["value"] for opt in select.find_all("option") if opt.get("value", "").strip()]


def _parse_item(li: Tag) -> MenuItem:
    li_classes = li.get("class", [])

    name_tag = li.find(class_="clsLabel_Name")
    name = name_tag.get_text(strip=True) if name_tag else "Unknown"

    ingredients = ""
    ing_tag = li.find(class_="clsLabel_Ingredients")
    if ing_tag:
        ing_copy = BeautifulSoup(str(ing_tag), "lxml").find(class_="clsLabel_Ingredients")
        for section_span in ing_copy.find_all(class_="clsSectionName"):
            section_span.extract()
        ingredients = ing_copy.get_text(strip=True)

    allergens = ""
    al_tag = li.find(class_="clsLabel_Allergens")
    if al_tag:
        al_copy = BeautifulSoup(str(al_tag), "lxml").find(class_="clsLabel_Allergens")
        for section_span in al_copy.find_all(class_="clsSectionNameAllegens"):
            section_span.extract()
        allergens = al_copy.get_text(strip=True)

    trace_allergens = ""
    tr_tag = li.find(class_="clsLabel_TraceAllergens")
    if tr_tag:
        tr_copy = BeautifulSoup(str(tr_tag), "lxml").find(class_="clsLabel_TraceAllergens")
        for section_span in tr_copy.find_all(class_="clsSectionNameAllegens"):
            section_span.extract()
        trace_allergens = tr_copy.get_text(strip=True)

    return MenuItem(
        name=name,
        ingredients=ingredients,
        allergens=allergens,
        trace_allergens=trace_allergens,
        is_gluten_free="clsGF_Row" in li_classes,
        is_vegetarian="clsV_Row" in li_classes,
        is_vegan="clsVGN_Row" in li_classes,
        is_halal="clsHALAL_Row" in li_classes,
        is_kosher="clsKOSHER_Row" in li_classes,
    )


def parse_menu_html(html: str, hall_id: str, hall_name: str, date: str, meal: str) -> DiningHallMenu:
    soup = BeautifulSoup(html, "lxml")
    items = []
    raw_items = soup.find_all("li", class_="clsMenuItem")
    if not raw_items:
        # Check for known page-structure signals to distinguish "no menu posted" from
        # "site changed its HTML" — if the form dropdowns are still present, the scraper
        # is working but the hall has no items for this meal/date.
        has_form = bool(soup.find("select", {"id": "MainContent_lstLocations"}))
        if not has_form:
            logger.warning(
                "%s: Neither menu items nor the location dropdown found — "
                "Stanford may have changed the page structure. "
                "Check CSS class 'clsMenuItem' and form ID 'MainContent_lstLocations'.",
                hall_name,
            )
    for li in raw_items:
        try:
            items.append(_parse_item(li))
        except Exception as exc:
            logger.warning("Failed to parse menu item in %s: %s", hall_name, exc)
    return DiningHallMenu(hall_name=hall_name, hall_id=hall_id, date=date, meal=meal, items=items)


class StanfordMenuScraper:
    def __init__(self, timeout: int = 30, request_delay: float = 0.5):
        self.timeout = timeout
        self.request_delay = request_delay

    def _initial_get(self, session: requests.Session) -> Tuple[BeautifulSoup, Dict[str, str]]:
        resp = session.get(MENU_URL, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        return soup, _extract_hidden_fields(soup)

    def _post_menu(
        self,
        session: requests.Session,
        hidden: Dict[str, str],
        hall_id: str,
        date_str: str,
        meal: str,
    ) -> Tuple[str, Dict[str, str]]:
        data = {
            "__VIEWSTATE": hidden.get("__VIEWSTATE", ""),
            "__VIEWSTATEGENERATOR": hidden.get("__VIEWSTATEGENERATOR", ""),
            "__EVENTVALIDATION": hidden.get("__EVENTVALIDATION", ""),
            "__EVENTTARGET": "GetMenulstMealType",
            "__EVENTARGUMENT": "",
            "ctl00$MainContent$lstLocations": hall_id,
            "ctl00$MainContent$lstDay": date_str,
            "ctl00$MainContent$lstMealType": meal,
        }
        resp = session.post(
            MENU_URL, data=data,
            headers={"Referer": MENU_URL},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        new_hidden = _extract_hidden_fields(soup)
        # Carry forward unchanged fields
        for key in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
            if new_hidden.get(key):
                hidden[key] = new_hidden[key]
        return resp.text, hidden

    def get_available_options(self) -> Dict:
        session = _make_session()
        soup, _ = self._initial_get(session)
        locations = _extract_locations(soup)
        if not locations:
            raise RuntimeError("No dining hall locations found on page — site structure may have changed")
        days = _extract_day_options(soup)
        if not days:
            raise RuntimeError("No day options found on page — site structure may have changed")
        meals = _extract_meal_options(soup)
        return {"locations": locations, "days": days, "meals": meals}

    def scrape_hall(
        self,
        session: requests.Session,
        hidden: Dict[str, str],
        hall_id: str,
        hall_name: str,
        date_str: str,
        meal: str,
    ) -> Tuple[DiningHallMenu, Dict[str, str]]:
        html, updated_hidden = self._post_menu(session, hidden, hall_id, date_str, meal)
        menu = parse_menu_html(html, hall_id=hall_id, hall_name=hall_name, date=date_str, meal=meal)
        return menu, updated_hidden

    def scrape_all(
        self,
        date_str: str,
        meal: str,
        hall_ids: Optional[List[str]] = None,
    ) -> List[DiningHallMenu]:
        session = _make_session()
        soup, hidden = self._initial_get(session)

        locations = _extract_locations(soup)
        if not locations:
            raise RuntimeError("No dining halls found — the Stanford menu page may have changed")

        available_days = _extract_day_options(soup)
        if date_str not in available_days:
            raise RuntimeError(
                f"Date {date_str!r} not available on menu page. "
                f"Available: {available_days}"
            )

        available_meals = _extract_meal_options(soup)
        if meal not in available_meals:
            raise RuntimeError(
                f"Meal {meal!r} not available. Available meals: {available_meals}"
            )

        if hall_ids:
            locations = [(hid, hname) for hid, hname in locations if hid in hall_ids]
            if not locations:
                raise RuntimeError(f"None of the requested halls {hall_ids} are available")

        results: List[DiningHallMenu] = []
        for hall_id, hall_name in locations:
            try:
                logger.info("Scraping %s / %s / %s", hall_name, date_str, meal)
                menu, hidden = self.scrape_hall(
                    session, hidden, hall_id, hall_name, date_str, meal
                )
                results.append(menu)
                time.sleep(self.request_delay)
            except Exception as exc:
                logger.warning("Failed to scrape %s: %s", hall_name, exc)
                results.append(
                    DiningHallMenu(
                        hall_name=hall_name, hall_id=hall_id,
                        date=date_str, meal=meal, items=[],
                    )
                )

        return results
