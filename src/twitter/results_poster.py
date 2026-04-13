"""
Post nightly results to Twitter/X with a results card image.

Usage:
    from src.twitter.results_poster import post_nightly_results
    post_nightly_results()

Requires TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN,
TWITTER_ACCESS_TOKEN_SECRET in .env.
"""

from __future__ import annotations

import json
import os
import random
from pathlib import Path

from src.twitter.results_card import generate_results_card
from src.betting.units import fmt_u

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"


def _get_clients():
    """Create tweepy Client (v2) and API (v1.1) for media upload."""
    try:
        import tweepy
    except ImportError:
        print("  ERROR: tweepy not installed")
        return None, None

    api_key = os.getenv("TWITTER_API_KEY", "")
    api_secret = os.getenv("TWITTER_API_SECRET", "")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN", "")
    access_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET", "")

    if not all([api_key, api_secret, access_token, access_secret]):
        print("  ERROR: Twitter API credentials not set")
        return None, None

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api = tweepy.API(auth)
    return client, api


def _load_today_results() -> tuple[dict | None, dict | None]:
    """Load today's results and season stats."""
    if not RESULTS_PATH.exists():
        return None, None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        return None, None

    today = results[-1]

    total_wins = sum(r.get("wins", 0) for r in results)
    total_losses = sum(r.get("losses", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    total_profit = sum(r.get("day_profit", 0) for r in results)
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0

    season = {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
        "bankroll": round(10000.0 + total_profit, 2),
        "days": len(results),
    }

    return today, season


def format_results_tweet(today: dict, season: dict) -> str:
    """Build tweet text for nightly results."""
    wins = today.get("wins", 0)
    losses = today.get("losses", 0)
    profit = today.get("day_profit", 0)
    date = today.get("date", "")

    sign = "+" if profit >= 0 else ""
    u = fmt_u(profit, signed=True)
    pnl = f"{u} ({sign}${abs(profit):,.0f})"

    # Flavor line based on performance
    if wins > 0 and losses == 0:
        flavors = [
            f"Perfect {wins}-0 night.",
            f"Clean sweep — {wins} for {wins}.",
            f"Flawless. {wins}-0. The model doesn't miss.",
        ]
    elif losses > 0 and wins == 0:
        flavors = [
            f"Rough night. 0-{losses}. On to tomorrow.",
            f"Variance hit us tonight. 0-{losses}.",
        ]
    elif profit > 500:
        flavors = [
            f"Big night. {wins}-{losses} for {pnl}.",
            f"The model ate tonight. {wins}-{losses}, {pnl}.",
            f"Underdogs cashing. {wins}-{losses} for {pnl}.",
        ]
    elif profit > 0:
        flavors = [
            f"Solid night: {wins}-{losses} ({pnl}).",
            f"Another green day: {wins}-{losses} for {pnl}.",
        ]
    else:
        flavors = [
            f"Down {pnl} tonight ({wins}-{losses}). Long season.",
            f"{wins}-{losses} for {pnl}. Shake it off.",
        ]

    parts = [random.choice(flavors)]

    # Season line (units-first)
    sw, sl = season["wins"], season["losses"]
    sp = season["total_profit"]
    sp_sign = "+" if sp >= 0 else ""
    bankroll = season.get("bankroll", 10000)
    parts.append(
        f"Season: {sw}-{sl} | {fmt_u(sp, signed=True)} ({sp_sign}${abs(sp):,.0f}) | {season['roi']}% ROI\n"
        f"100u → {fmt_u(bankroll)} ($10K → ${bankroll:,.0f})"
    )

    parts.append("Full results: ozzyanalytics.com/results.html\n\n#MLB #SportsBetting #MLBPicks")

    return "\n\n".join(parts)


def post_nightly_results():
    """Grade today, generate results card, and tweet."""
    today, season = _load_today_results()
    if not today or not today.get("picks"):
        print("  No results to post.")
        return

    client, api = _get_clients()
    if not client or not api:
        return

    # Generate image
    card_path = generate_results_card(today, season)

    # Build tweet
    tweet_text = format_results_tweet(today, season)
    print(f"  Results tweet ({len(tweet_text)} chars):")
    print(f"  ---")
    for line in tweet_text.split("\n"):
        print(f"  {line}")
    print(f"  ---")

    try:
        media = api.media_upload(str(card_path))
        response = client.create_tweet(text=tweet_text, media_ids=[media.media_id])
        tweet_id = response.data["id"]
        print(f"  Posted: https://x.com/Ozzy_Analytics/status/{tweet_id}")
    except Exception as e:
        print(f"  ERROR posting results tweet: {e}")
        try:
            response = client.create_tweet(text=tweet_text)
            tweet_id = response.data["id"]
            print(f"  Posted (text only): https://x.com/Ozzy_Analytics/status/{tweet_id}")
        except Exception as e2:
            print(f"  ERROR posting text-only: {e2}")
