"""
Post daily picks to Twitter/X via tweepy.

Usage:
    from src.twitter.poster import post_daily_picks
    post_daily_picks(picks_data)

Requires TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN,
TWITTER_ACCESS_TOKEN_SECRET in .env.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"


def _get_client():
    """Create an authenticated tweepy Client."""
    try:
        import tweepy
    except ImportError:
        print("  ERROR: tweepy package not installed. Run: pip install tweepy")
        return None

    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        print("  ERROR: Twitter API credentials not set in .env")
        return None

    return tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )


def _load_yesterday_results() -> dict | None:
    """Load the most recent day's results from results.json."""
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    return results[-1] if results else None


def _load_season_stats() -> dict | None:
    """Compute season-level stats from results log."""
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        return None

    total_wins = sum(r.get("wins", 0) for r in results)
    total_losses = sum(r.get("losses", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    total_profit = sum(r.get("day_profit", 0) for r in results)
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0
    bankroll = round(10000.0 + total_profit, 2)

    return {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
        "bankroll": bankroll,
    }


def _format_recap(yesterday: dict) -> str:
    """Format yesterday's results into a recap line."""
    wins = yesterday.get("wins", 0)
    losses = yesterday.get("losses", 0)
    profit = yesterday.get("day_profit", 0)
    picks = yesterday.get("picks", [])

    if not picks:
        return ""

    # Header line
    sign = "+" if profit >= 0 else "-"
    lines = [f"Yesterday: {wins}-{losses} ({sign}${abs(profit):.0f})"]

    # Individual results
    for p in picks:
        result = "W" if p.get("won") else "L"
        pick_name = p.get("pick", "")
        score = p.get("actual_score", "")
        pnl = p.get("profit", 0)
        sign = "+" if pnl >= 0 else ""
        lines.append(f"  {result} {pick_name} ({score}) {sign}${pnl:.0f}")

    return "\n".join(lines)


def _format_pick_line(pick: dict) -> str:
    """Format a single pick into a compact line."""
    name = pick.get("pick", "")
    odds = pick.get("odds", "")
    edge = pick.get("edge_pct", 0)
    prob = pick.get("model_prob", 0)
    return f"{name} ({odds}) | {prob:.0%} win | {edge:.1f}% edge"


def format_tweet(picks_data: dict) -> str:
    """Build a single tweet with yesterday's recap + today's picks + season stats."""
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks = picks_data.get("picks", [])

    parts = []

    # Yesterday's recap
    yesterday = _load_yesterday_results()
    if yesterday:
        recap = _format_recap(yesterday)
        if recap:
            parts.append(recap)

    # Today's picks
    if picks:
        pick_lines = [_format_pick_line(p) for p in picks]
        parts.append(f"Today's Picks ({date}):\n" + "\n".join(pick_lines))
    else:
        parts.append(f"No edges today ({date}). The model is sitting tight.")

    # Season stats
    stats = _load_season_stats()
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        roi = stats["roi"]
        sign = "+" if profit >= 0 else "-"
        parts.append(f"Season: {w}-{l} | {sign}${abs(profit):.0f} | {roi}% ROI")

    # Site link + hashtags
    parts.append("Full analysis: ozzyanalytics.com\n\n#MLB #SportsBetting #MLBPicks")

    tweet = "\n\n".join(parts)

    # Twitter limit is 280 chars — trim picks if needed
    if len(tweet) > 280:
        tweet = _trim_tweet(picks_data, yesterday, stats)

    return tweet


def _trim_tweet(picks_data: dict, yesterday: dict | None, stats: dict | None) -> str:
    """Build a shorter tweet if the full version exceeds 280 chars."""
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks = picks_data.get("picks", [])

    parts = []

    # Shorter recap
    if yesterday:
        wins = yesterday.get("wins", 0)
        losses = yesterday.get("losses", 0)
        profit = yesterday.get("day_profit", 0)
        sign = "+" if profit >= 0 else "-"
        parts.append(f"Yesterday: {wins}-{losses} ({sign}${abs(profit):.0f})")

    # Compact picks (no win% or edge)
    if picks:
        pick_lines = []
        for p in picks:
            name = p.get("pick", "")
            odds = p.get("odds", "")
            pick_lines.append(f"{name} ({odds})")
        parts.append(f"Picks ({date}):\n" + "\n".join(pick_lines))

    # Season line
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        sign = "+" if profit >= 0 else "-"
        parts.append(f"Season: {w}-{l} | {sign}${abs(profit):.0f}")

    parts.append("ozzyanalytics.com\n\n#MLB #SportsBetting #MLBPicks")

    return "\n\n".join(parts)


def post_daily_picks(picks_data: dict):
    """Post today's picks to Twitter."""
    client = _get_client()
    if not client:
        return

    tweet_text = format_tweet(picks_data)
    print(f"  Tweet ({len(tweet_text)} chars):")
    print(f"  ---")
    for line in tweet_text.split("\n"):
        print(f"  {line}")
    print(f"  ---")

    try:
        response = client.create_tweet(text=tweet_text)
        tweet_id = response.data["id"]
        print(f"  Posted: https://x.com/Ozzy_Analytics/status/{tweet_id}")
    except Exception as e:
        print(f"  ERROR posting to Twitter: {e}")
