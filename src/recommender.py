from __future__ import annotations

import json
import logging
import os
from typing import List

from .models import Recommendation, ScoredHall, ScrapeResult
from .preferences import load as load_prefs
from .scorer import build_deterministic_recommendation

logger = logging.getLogger(__name__)


def _build_system_prompt() -> str:
    prefs = load_prefs()
    description = prefs["description"].strip()
    notes = "\n".join(f"- {note}" for note in prefs["notes"])

    hard_avoids = [
        r["category"].replace("_", " ")
        for r in prefs["scoring"]["penalties"]
        if abs(float(r["score"])) >= 4.5
    ]
    hard_avoid_str = ", ".join(hard_avoids) if hard_avoids else "none specified"

    return f"""\
You are a personal nutrition advisor for a Stanford student.

Goal: {description}

Specific rules:
{notes}

Hard avoids (never recommend these even as secondary options): {hard_avoid_str}

You will receive JSON with pre-scored dining hall menus. The scores already reflect the \
preferences above. Use them as a strong signal but apply your own judgement too.

Return a concise recommendation as JSON with exactly these keys:
- best_hall: string
- backup_hall: string
- recommended_plate: list of strings (specific dishes to eat, max 6)
- avoid: list of strings (specific dishes to skip, max 4)
- confidence: "high", "medium", or "low"
- reasoning: 2-3 sentence explanation

Only return valid JSON. No markdown. No explanations outside the JSON."""


def _build_ai_payload(scrape: ScrapeResult, scored: List[ScoredHall]) -> str:
    menu_map = {m.hall_id: m for m in scrape.halls}
    halls_summary = []
    for hall in scored:
        menu = menu_map.get(hall.hall_id)
        items_list = (
            [{"name": it.name, "ingredients": it.ingredients, "allergens": it.allergens}
             for it in menu.items]
            if menu else []
        )
        halls_summary.append({
            "hall": hall.hall_name,
            "score": hall.total_score,
            "positive_highlights": list(hall.breakdown.get("positive_hits", {}).keys()),
            "penalty_flags": list(hall.breakdown.get("penalty_hits", {}).keys()),
            "top_items": [it.name for it in hall.top_items[:6]],
            "avoid_items": [it.name for it in hall.avoid_items[:4]],
            "all_items": items_list,
        })
    return json.dumps(
        {"date": scrape.date, "meal": scrape.meal, "halls": halls_summary},
        indent=2,
    )


def generate_ai_recommendation(
    scrape: ScrapeResult,
    scored: List[ScoredHall],
) -> Recommendation:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError("OPENAI_API_KEY environment variable not set")

    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — run: pip install openai")

    client = OpenAI(api_key=api_key)
    system_prompt = _build_system_prompt()
    payload = _build_ai_payload(scrape, scored)

    logger.info("Calling OpenAI API (payload: %d chars)", len(payload))
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ],
        max_tokens=800,
        temperature=0.2,
    )

    raw = response.choices[0].message.content.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OpenAI returned invalid JSON: {exc}\nRaw: {raw}") from exc

    required_keys = {"best_hall", "backup_hall", "recommended_plate", "avoid", "confidence", "reasoning"}
    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(f"OpenAI response missing keys: {missing}")

    return Recommendation(
        best_hall=data["best_hall"],
        backup_hall=data["backup_hall"],
        recommended_plate=data["recommended_plate"],
        avoid=data["avoid"],
        confidence=data["confidence"],
        reasoning=data["reasoning"],
        scored_halls=scored,
        ai_generated=True,
        date=scrape.date,
        meal=scrape.meal,
    )


def get_recommendation(
    scrape: ScrapeResult,
    scored: List[ScoredHall],
    use_ai: bool = True,
) -> Recommendation:
    if use_ai:
        try:
            return generate_ai_recommendation(scrape, scored)
        except Exception as exc:
            logger.warning("AI recommendation failed (%s), falling back to scoring", exc)

    return build_deterministic_recommendation(scored, date=scrape.date, meal=scrape.meal)
