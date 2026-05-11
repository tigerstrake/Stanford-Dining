from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

CONFIG_PATH = Path(__file__).parent.parent / "config" / "preferences.yml"


@lru_cache(maxsize=1)
def load() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Preferences config not found at {CONFIG_PATH}. "
            "Expected config/preferences.yml in the project root."
        )
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    _validate(data)
    return data


def _validate(data: Dict[str, Any]) -> None:
    required = {"description", "notes", "scoring"}
    missing = required - set(data)
    if missing:
        raise ValueError(f"preferences.yml is missing required keys: {missing}")
    scoring = data.get("scoring", {})
    if "bonuses" not in scoring or "penalties" not in scoring:
        raise ValueError("preferences.yml scoring section must have 'bonuses' and 'penalties'")
    for rule in scoring["bonuses"] + scoring["penalties"]:
        for field in ("category", "score", "keywords"):
            if field not in rule:
                raise ValueError(
                    f"Each scoring rule must have '{field}'. Offending rule: {rule}"
                )
