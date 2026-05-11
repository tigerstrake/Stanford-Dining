from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class MenuItem(BaseModel):
    name: str
    ingredients: str = ""
    allergens: str = ""
    trace_allergens: str = ""
    is_gluten_free: bool = False
    is_vegetarian: bool = False
    is_vegan: bool = False
    is_halal: bool = False
    is_kosher: bool = False


class DiningHallMenu(BaseModel):
    hall_name: str
    hall_id: str
    date: str
    meal: str
    items: List[MenuItem] = Field(default_factory=list)


class ScrapeResult(BaseModel):
    date: str
    meal: str
    halls: List[DiningHallMenu] = Field(default_factory=list)
    scraped_at: str
    reliable: bool = True
    warnings: List[str] = Field(default_factory=list)


class ItemScore(BaseModel):
    name: str
    score: float
    tags: List[str] = Field(default_factory=list)


class ScoredHall(BaseModel):
    hall_name: str
    hall_id: str
    total_score: float
    breakdown: dict
    top_items: List[ItemScore] = Field(default_factory=list)
    avoid_items: List[ItemScore] = Field(default_factory=list)
    item_count: int = 0


class Recommendation(BaseModel):
    best_hall: str
    backup_hall: str
    recommended_plate: List[str]
    avoid: List[str]
    confidence: str
    reasoning: str
    scored_halls: List[ScoredHall] = Field(default_factory=list)
    ai_generated: bool = False
    date: str
    meal: str
