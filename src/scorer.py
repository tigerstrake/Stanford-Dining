from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .models import DiningHallMenu, ItemScore, MenuItem, ScoredHall
from .preferences import load as load_prefs

# Rule = (category_name, score_delta, [keywords])
Rule = Tuple[str, float, List[str]]


def _build_rules() -> Tuple[List[Rule], List[Rule]]:
    scoring = load_prefs()["scoring"]
    bonuses: List[Rule] = [
        (r["category"], float(r["score"]), [kw.lower() for kw in r["keywords"]])
        for r in scoring["bonuses"]
    ]
    penalties: List[Rule] = [
        (r["category"], float(r["score"]), [kw.lower() for kw in r["keywords"]])
        for r in scoring["penalties"]
    ]
    return bonuses, penalties


def _text_for_matching(item: MenuItem) -> str:
    return f"{item.name} {item.ingredients}".lower()


def _match_keywords(text: str, keywords: List[str]) -> bool:
    for kw in keywords:
        # Allow a trailing plural 's' so "lentil" matches "lentils"
        pattern = r"\b" + re.escape(kw) + r"s?\b"
        if re.search(pattern, text):
            return True
    return False


def score_item(item: MenuItem) -> Tuple[float, List[str]]:
    bonuses, penalties = _build_rules()
    text = _text_for_matching(item)
    total = 0.0
    tags: List[str] = []

    for category, delta, keywords in bonuses:
        if _match_keywords(text, keywords):
            total += delta
            tags.append(f"+{category}")

    for category, delta, keywords in penalties:
        if _match_keywords(text, keywords):
            total += delta
            tags.append(f"-{category}")

    return total, tags


def score_hall(menu: DiningHallMenu) -> ScoredHall:
    if not menu.items:
        return ScoredHall(
            hall_name=menu.hall_name,
            hall_id=menu.hall_id,
            total_score=0.0,
            breakdown={},
            item_count=0,
        )

    positive_hits: Dict[str, int] = {}
    penalty_hits: Dict[str, int] = {}
    scored_items: List[ItemScore] = []

    for item in menu.items:
        raw_score, tags = score_item(item)
        scored_items.append(ItemScore(name=item.name, score=raw_score, tags=tags))
        for tag in tags:
            if tag.startswith("+"):
                k = tag[1:]
                positive_hits[k] = positive_hits.get(k, 0) + 1
            else:
                k = tag[1:]
                penalty_hits[k] = penalty_hits.get(k, 0) + 1

    scored_items.sort(key=lambda x: x.score, reverse=True)
    top_items = [it for it in scored_items if it.score > 0][:8]
    avoid_items = [it for it in scored_items if it.score < 0]

    return ScoredHall(
        hall_name=menu.hall_name,
        hall_id=menu.hall_id,
        total_score=round(sum(it.score for it in scored_items), 2),
        breakdown={"positive_hits": positive_hits, "penalty_hits": penalty_hits},
        top_items=top_items,
        avoid_items=avoid_items,
        item_count=len(menu.items),
    )


def score_all_halls(menus: List[DiningHallMenu]) -> List[ScoredHall]:
    scored = [score_hall(m) for m in menus]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored


def build_deterministic_recommendation(scored: List[ScoredHall], date: str, meal: str):
    from .models import Recommendation

    halls_with_items = [s for s in scored if s.item_count > 0]
    if not halls_with_items:
        return Recommendation(
            best_hall="None",
            backup_hall="None",
            recommended_plate=[],
            avoid=[],
            confidence="low",
            reasoning="No menu data available for any dining hall.",
            scored_halls=scored,
            ai_generated=False,
            date=date,
            meal=meal,
        )

    best = halls_with_items[0]
    backup = halls_with_items[1] if len(halls_with_items) > 1 else best

    recommended_plate = [it.name for it in best.top_items[:5]]
    avoid = [it.name for it in best.avoid_items[:3]]

    confidence = "high" if best.total_score > 10 else ("medium" if best.total_score > 3 else "low")

    top_tags = sorted(
        best.breakdown.get("positive_hits", {}).items(),
        key=lambda x: x[1],
        reverse=True,
    )
    tag_summary = ", ".join(t for t, _ in top_tags[:3]) if top_tags else "varied options"

    reasoning = (
        f"{best.hall_name} scores highest ({best.total_score:.1f} pts) "
        f"with strong offerings in: {tag_summary}. "
        f"Backup: {backup.hall_name} ({backup.total_score:.1f} pts)."
    )

    return Recommendation(
        best_hall=best.hall_name,
        backup_hall=backup.hall_name,
        recommended_plate=recommended_plate,
        avoid=avoid,
        confidence=confidence,
        reasoning=reasoning,
        scored_halls=scored,
        ai_generated=False,
        date=date,
        meal=meal,
    )
