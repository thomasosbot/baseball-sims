"""
Deploy TikTok video to the Netlify site for easy download.

Copies the generated video + caption to site/public/tiktok/ so it's
accessible at ozzyanalytics.com/tiktok/latest.mp4.

Usage:
    from src.tiktok.poster import deploy_video
    deploy_video("data/tiktok/2026-03-27.mp4", picks_data)
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

SITE_TIKTOK_DIR = Path(__file__).parent.parent.parent / "site" / "public" / "tiktok"


def format_caption(picks_data: dict) -> str:
    """Build a TikTok caption with hashtags."""
    date = picks_data.get("date", "")
    picks = picks_data.get("picks", [])
    num = len(picks)

    lines = [
        f"Today's MLB Picks - {date}",
        f"{num} edge{'s' if num != 1 else ''} found by 10,000 simulations",
        "",
        "Full analysis at ozzyanalytics.com",
        "",
        "#MLB #BaseballPicks #SportsBetting #MLBPicks #BaseballBetting "
        "#SportsAnalytics #BettingPicks #MLBBetting",
    ]
    return "\n".join(lines)


def deploy_video(video_path: str | Path, picks_data: dict) -> Path | None:
    """
    Copy video to site/public/tiktok/ for Netlify deployment.

    Creates:
        site/public/tiktok/latest.mp4   — always the most recent video
        site/public/tiktok/<date>.mp4    — archived by date
        site/public/tiktok/caption.txt   — ready-to-paste TikTok caption

    Returns:
        Path to the deployed video, or None on failure.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        print(f"  ERROR: Video not found: {video_path}")
        return None

    SITE_TIKTOK_DIR.mkdir(parents=True, exist_ok=True)

    date = picks_data.get("date", "unknown")

    # Copy as latest.mp4 and date-specific archive
    latest = SITE_TIKTOK_DIR / "latest.mp4"
    dated = SITE_TIKTOK_DIR / f"{date}.mp4"
    shutil.copy2(video_path, latest)
    shutil.copy2(video_path, dated)

    # Write caption for easy copy-paste
    caption = format_caption(picks_data)
    caption_path = SITE_TIKTOK_DIR / "caption.txt"
    caption_path.write_text(caption)

    print(f"  TikTok video deployed:")
    print(f"    Video: ozzyanalytics.com/tiktok/latest.mp4")
    print(f"    Caption: ozzyanalytics.com/tiktok/caption.txt")
    print(f"  ---")
    for line in caption.split("\n"):
        print(f"  {line}")
    print(f"  ---")

    return latest
