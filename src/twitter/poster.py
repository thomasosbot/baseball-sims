"""
Post daily picks to Twitter/X via tweepy with a pick card image.

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

from src.twitter.card import generate_pick_card

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"


def _get_clients():
    """Create tweepy Client (v2) and API (v1.1) for media upload."""
    try:
        import tweepy
    except ImportError:
        print("  ERROR: tweepy package not installed. Run: pip install tweepy")
        return None, None

    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        print("  ERROR: Twitter API credentials not set in .env")
        return None, None

    # v2 Client for tweeting
    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    # v1.1 API for media upload (v2 doesn't support media upload)
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api = tweepy.API(auth)

    return client, api


def _load_season_stats() -> dict | None:
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        return None

    total_wins = sum(r.get("wins", 0) for r in results)
    total_losses = sum(r.get("losses", 0) for r in results)
    total_profit = sum(r.get("day_profit", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0

    return {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
    }


def format_tweet(picks_data: dict) -> str:
    """Build tweet text (shorter now since the image carries the detail)."""
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks = picks_data.get("picks", [])
    # Deduplicate
    seen = set()
    unique = []
    for p in picks:
        key = p.get("pick", "")
        if key not in seen:
            seen.add(key)
            unique.append(p)
    picks = unique

    parts = []

    # Season line
    stats = _load_season_stats()
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        sign = "+" if profit >= 0 else "-"
        parts.append(f"Season: {w}-{l} | {sign}${abs(profit):.0f} | {stats['roi']}% ROI")

    # Pick summary
    if picks:
        num = len(picks)
        parts.append(f"{num} pick{'s' if num != 1 else ''} for {date}")
    else:
        parts.append(f"No edges today ({date})")

    parts.append("Full analysis: ozzyanalytics.com\n\n#MLB #SportsBetting #MLBPicks")

    return "\n\n".join(parts)


def post_daily_picks(picks_data: dict):
    """Generate pick card image and post to Twitter."""
    client, api = _get_clients()
    if not client or not api:
        return

    # Generate the image
    card_path = generate_pick_card(picks_data)

    # Build tweet text
    tweet_text = format_tweet(picks_data)
    print(f"  Tweet ({len(tweet_text)} chars):")
    print(f"  ---")
    for line in tweet_text.split("\n"):
        print(f"  {line}")
    print(f"  ---")

    try:
        # Upload media via v1.1 API
        media = api.media_upload(str(card_path))
        media_id = media.media_id
        print(f"  Media uploaded: {media_id}")

        # Post tweet with image via v2 Client
        response = client.create_tweet(text=tweet_text, media_ids=[media_id])
        tweet_id = response.data["id"]
        print(f"  Posted: https://x.com/Ozzy_Analytics/status/{tweet_id}")
    except Exception as e:
        print(f"  ERROR posting to Twitter: {e}")
        # Fallback: try text-only tweet
        try:
            print(f"  Falling back to text-only tweet...")
            response = client.create_tweet(text=tweet_text)
            tweet_id = response.data["id"]
            print(f"  Posted (text only): https://x.com/Ozzy_Analytics/status/{tweet_id}")
        except Exception as e2:
            print(f"  ERROR posting text-only tweet: {e2}")
