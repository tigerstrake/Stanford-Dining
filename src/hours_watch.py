"""Notice when the dining hours change (e.g. Fall hours give way to Winter).

The send times in deploy/systemd/stanford-dining.timer were worked out by hand
from the current hours: 30 minutes before the earliest hall opens for each
meal. When the hours page changes, the scheduled run sends one Telegram notice
with the new openings and the send times they imply, so the timer gets updated
instead of quietly going stale. The last hours seen live in
data/hours_state.json.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import DiningHours, HallHours

logger = logging.getLogger(__name__)

SEND_LEAD_MINUTES = 30

# A parse that suddenly finds this many fewer halls is a broken page, not new hours.
MAX_HALLS_LOST = 2

_START_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?", re.I)
_BLOCK_LABELS = {"weekday": "Mon–Fri", "weekend": "Sat–Sun"}


def state_path(data_dir: str) -> Path:
    return Path(data_dir) / "hours_state.json"


def _halls(hours: DiningHours) -> Dict[str, HallHours]:
    return {k: h if isinstance(h, HallHours) else HallHours(**h) for k, h in hours.halls.items()}


def start_minutes(window: str) -> Optional[int]:
    """Minutes after midnight that "7:30 a.m. - 10:00 a.m." opens, else None."""
    m = _START_RE.match(window or "")
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)) % 12, int(m.group(2) or 0), m.group(3).lower()
    return (hour + (12 if ampm == "p" else 0)) * 60 + minute


def earliest_openings(hours: DiningHours) -> Dict[str, Dict[str, int]]:
    """{"weekday": {"Breakfast": 450, ...}, "weekend": {"Brunch/Lunch": 570, ...}}."""
    out: Dict[str, Dict[str, int]] = {"weekday": {}, "weekend": {}}
    for hall in _halls(hours).values():
        for block in ("weekday", "weekend"):
            for meal, window in getattr(hall, block).items():
                if meal.lower().startswith("late"):
                    continue
                start = start_minutes(window)
                if start is not None and start < out[block].get(meal, 24 * 60):
                    out[block][meal] = start
    for block in out:
        out[block] = dict(sorted(out[block].items(), key=lambda kv: kv[1]))
    return out


def fingerprint(hours: DiningHours) -> str:
    body = {
        key: {"weekday": h.weekday, "weekend": h.weekend, "note": h.note, "late_night": h.late_night}
        for key, h in _halls(hours).items()
    }
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _notes(hours: DiningHours) -> List[str]:
    return sorted({h.note for h in _halls(hours).values() if h.note})


def _clock(minutes: int) -> str:
    hour, minute = divmod(minutes, 60)
    return f"{(hour - 1) % 12 + 1}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def build_alert(openings: Dict[str, Dict[str, int]], old_notes: List[str], new_notes: List[str],
                openings_changed: bool) -> str:
    lines = ["🕒 <b>Dining hours changed</b>"]
    if old_notes != new_notes:
        lines.append(f"Was: <i>{html.escape('; '.join(old_notes) or '—')}</i>")
        lines.append(f"Now: <i>{html.escape('; '.join(new_notes) or '—')}</i>")
    lines.append("")
    lines.append("Earliest openings now" + ("" if openings_changed else " (unchanged)") + ":")
    for block, meals in openings.items():
        if meals:
            lines.append(f"{_BLOCK_LABELS[block]}: " + " · ".join(f"{m} {_clock(t)}" for m, t in meals.items()))
    if openings_changed:
        lines.append("")
        lines.append(f"Sending {SEND_LEAD_MINUTES} min before each means:")
        for block, meals in openings.items():
            if meals:
                times = " · ".join(_clock(t - SEND_LEAD_MINUTES) for t in meals.values())
                lines.append(f"{_BLOCK_LABELS[block]}: {times}")
        lines.append("Update the OnCalendar= lines in deploy/systemd/stanford-dining.timer to match.")
    return "\n".join(lines)


def check_hours_change(hours: DiningHours, data_dir: str, save: bool = True) -> Optional[str]:
    """Alert text if the hours differ from the last ones seen, else None.

    The first run only records a baseline. With `save=False` (dry runs) the
    baseline is left alone, so the real run still sends the alert.
    """
    path = state_path(data_dir)
    previous = None
    if path.exists():
        try:
            previous = json.loads(path.read_text())
        except ValueError:
            logger.warning("Ignoring unreadable %s", path)

    current = {
        "fingerprint": fingerprint(hours),
        "notes": _notes(hours),
        "openings": earliest_openings(hours),
        "hall_count": len(hours.halls),
        "seen_at": datetime.now(timezone.utc).isoformat(),
    }

    alert = None
    if previous and previous.get("fingerprint") != current["fingerprint"]:
        if current["hall_count"] < previous.get("hall_count", 0) - MAX_HALLS_LOST:
            logger.warning(
                "Hours page parsed only %d halls (had %d); treating it as a page glitch, not new hours",
                current["hall_count"], previous.get("hall_count", 0),
            )
            return None
        alert = build_alert(
            current["openings"], previous.get("notes", []), current["notes"],
            openings_changed=previous.get("openings") != current["openings"],
        )
        logger.info("Dining hours changed since %s", previous.get("seen_at"))

    if save and (previous is None or alert is not None):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(current, indent=2))
    return alert
