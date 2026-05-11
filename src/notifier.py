from __future__ import annotations

import logging
import os
from typing import Optional

import requests

from .models import Recommendation

logger = logging.getLogger(__name__)


def format_recommendation_text(rec: Recommendation) -> str:
    lines = [
        f"**Stanford Dining Recommendation — {rec.meal.title()} on {rec.date}**",
        "",
        f"**Best hall:** {rec.best_hall}",
        f"**Backup hall:** {rec.backup_hall}",
        f"**Confidence:** {rec.confidence.upper()}",
        "",
        f"**Recommended plate:**",
    ]
    for item in rec.recommended_plate:
        lines.append(f"  • {item}")
    if rec.avoid:
        lines.append("")
        lines.append("**Skip:**")
        for item in rec.avoid:
            lines.append(f"  • {item}")
    lines.append("")
    lines.append(f"**Reasoning:** {rec.reasoning}")
    if not rec.ai_generated:
        lines.append("_(deterministic scoring — AI not used)_")
    return "\n".join(lines)


def format_recommendation_plain(rec: Recommendation) -> str:
    lines = [
        f"Stanford Dining Recommendation — {rec.meal.title()} on {rec.date}",
        "=" * 60,
        f"Best hall:    {rec.best_hall}",
        f"Backup hall:  {rec.backup_hall}",
        f"Confidence:   {rec.confidence.upper()}",
        "",
        "Recommended plate:",
    ]
    for item in rec.recommended_plate:
        lines.append(f"  - {item}")
    if rec.avoid:
        lines.append("")
        lines.append("Skip:")
        for item in rec.avoid:
            lines.append(f"  - {item}")
    lines.append("")
    lines.append(f"Reasoning: {rec.reasoning}")
    if not rec.ai_generated:
        lines.append("(deterministic scoring — AI not used)")
    return "\n".join(lines)


def send_discord(rec: Recommendation, webhook_url: Optional[str] = None) -> bool:
    url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping Discord notification")
        return False

    content = format_recommendation_text(rec)
    # Discord has a 2000-char message limit
    if len(content) > 1990:
        content = content[:1990] + "…"

    try:
        resp = requests.post(url, json={"content": content}, timeout=15)
        resp.raise_for_status()
        logger.info("Discord notification sent")
        return True
    except Exception as exc:
        logger.error("Discord notification failed: %s", exc)
        return False


def notify(rec: Recommendation, dry_run: bool = False) -> None:
    plain = format_recommendation_plain(rec)
    print(plain)

    if dry_run:
        logger.info("Dry-run mode: skipping Discord webhook")
        return

    success = send_discord(rec)
    if not success:
        logger.info("Recommendation printed to stdout as fallback")
