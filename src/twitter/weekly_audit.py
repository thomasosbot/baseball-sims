"""
Weekly 'transparency flex' tweet — rotates between three formats:
  Week 1: Tout Audit (weekly stats + accountability callout)
  Week 2: Math vs Vibes (real edge math vs "lock" fantasy)
  Week 3: Ask Your Capper (question + our answer)

Fires every Sunday via the nightly workflow.

Usage:
    from src.twitter.weekly_audit import post_weekly_audit
    post_weekly_audit()
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path

from src.betting.units import fmt_u

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"


def _get_clients():
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
    return client, None


def _load_results() -> list[dict]:
    if not RESULTS_PATH.exists():
        return []
    with open(RESULTS_PATH) as f:
        return json.load(f)


def _season_stats(results: list[dict]) -> dict:
    total_w = sum(r.get("wins", 0) for r in results)
    total_l = sum(r.get("losses", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    total_profit = sum(r.get("day_profit", 0) for r in results)
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0
    bankroll = round(10000.0 + total_profit, 2)
    win_rate = round(total_w / max(1, total_w + total_l) * 100, 1)
    return {
        "wins": total_w, "losses": total_l, "profit": total_profit,
        "roi": roi, "bankroll": bankroll, "win_rate": win_rate,
        "days": len(results), "total_picks": total_w + total_l,
    }


def _week_stats(results: list[dict]) -> dict:
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week = [r for r in results if r.get("date", "") > cutoff]
    if not week:
        return None
    w = sum(r.get("wins", 0) for r in week)
    l = sum(r.get("losses", 0) for r in week)
    profit = sum(r.get("day_profit", 0) for r in week)
    return {"wins": w, "losses": l, "profit": profit, "days": len(week)}


# ── Format 1: Tout Audit ──

AUDIT_JABS = [
    "Another week. Every pick tracked. Every loss shown.\n\nCan your capper say the same?",
    "No deleted tweets. No cherry-picked screenshots. No DMs.\n\nJust a public record that can't be faked.",
    "Full transparency, every single week. This is what accountability looks like.\n\nIf your capper won't do this, ask yourself why.",
    "We don't hide bad weeks. We post them. Because that's how you know the good weeks are real.",
    "Week in review — every pick, win or lose, tracked in public.\n\nYour \"locks\" guy can't relate.",
]


def _format_audit(week: dict, season: dict) -> str:
    ws = f"{week['wins']}W-{week['losses']}L"
    wp = fmt_u(week["profit"], signed=True)
    sp = fmt_u(season["profit"], signed=True)

    jab = random.choice(AUDIT_JABS)

    return (
        f"📋 Weekly Audit\n\n"
        f"This week: {ws} | {wp}\n"
        f"Season: {season['wins']}-{season['losses']} | {sp} | {season['roi']}% ROI\n\n"
        f"{jab}"
    )


# ── Format 2: Math vs Vibes ──

def _format_math(season: dict) -> str:
    templates = [
        (
            "A {wr}% win rate at average underdog odds is a {roi}% ROI.\n\n"
            "A 75% \"lock rate\" would make you richer than every hedge fund on earth.\n\n"
            "One of these is real. We're sitting at {sp} on {n} tracked picks.\n\n"
            "The other exists exclusively in DMs and deleted tweets."
        ),
        (
            "The math on \"locks\":\n\n"
            "• Best MLB team wins ~59% of games\n"
            "• Best models find 3-10% edges\n"
            "• Compounding small edges = real profit\n\n"
            "Our actual numbers: {wr}% | {sp} | {roi}% ROI on {n} picks\n\n"
            "Nobody is hitting 80%. Nobody. The math doesn't allow it."
        ),
        (
            "Edge = model probability − market probability.\n\n"
            "Our average edge is ~7%. That's tiny. That's also how you make {sp} "
            "on {n} picks at {roi}% ROI.\n\n"
            "\"Locks\" imply 90%+ confidence. In baseball. "
            "Where a 100-win team loses 62 games a year.\n\n"
            "Do the math. Or don't — just check our public record."
        ),
    ]
    t = random.choice(templates)
    return t.format(
        wr=season["win_rate"],
        roi=season["roi"],
        sp=fmt_u(season["profit"], signed=True),
        n=season["total_picks"],
    )


# ── Format 3: Ask Your Capper ──

CAPPER_QS = [
    {
        "q": "What's your Brier score?",
        "a": "Ours is 0.243 (out-of-sample 2025 backtest). Lower = better calibrated probabilities.",
        "explain": "Brier score measures how well predicted probabilities match reality. Most touts don't know what it is.",
    },
    {
        "q": "What's your season ROI?",
        "a": "Ours is {roi}% on {n} picks. Posted publicly. Auditable.",
        "explain": "ROI = profit / total wagered. Not win rate. Not \"units won.\" Actual return on investment.",
    },
    {
        "q": "What's your sample size?",
        "a": "Ours is {n} tracked picks across {days} days. Every one public before first pitch.",
        "explain": "10 picks means nothing. 50 picks means almost nothing. Edges only prove out over hundreds of bets.",
    },
    {
        "q": "How do you size your bets?",
        "a": "Quarter-Kelly: f* = 0.25 × (bp−q)/b. Sized by edge, not \"confidence level\" vibes.",
        "explain": "Kelly criterion is the mathematically optimal bet size. If your capper says \"5u max play\" without explaining why, it's theater.",
    },
    {
        "q": "Can I see every pick you've ever made?",
        "a": "Ours: ozzyanalytics.com/results.html — every pick, every loss, every dollar tracked.",
        "explain": "If the answer is \"check my DMs\" or \"I'll send screenshots,\" the record doesn't exist.",
    },
    {
        "q": "What's your methodology?",
        "a": "10,000 Monte Carlo sims per game. Multiplicative odds-ratio PA model. Bayesian regression. It's all on the site.",
        "explain": "\"I watch a lot of baseball\" is not a methodology. \"Trust me\" is not a methodology.",
    },
    {
        "q": "What's your CLV (Closing Line Value)?",
        "a": "We track opening vs closing line movement on every pick. Positive CLV = real edge, not luck.",
        "explain": "CLV is the gold standard for whether a bettor has real skill or is just running hot. Ask your capper. Watch them Google it.",
    },
]


def _format_ask_capper(season: dict) -> str:
    q = random.choice(CAPPER_QS)
    answer = q["a"].format(roi=season["roi"], n=season["total_picks"], days=season["days"])
    return (
        f"Ask your capper:\n\n"
        f"\"{q['q']}\"\n\n"
        f"{q['explain']}\n\n"
        f"Our answer: {answer}\n\n"
        f"📊⚾"
    )


# ── Main ──

def post_weekly_audit():
    """Post the weekly transparency tweet. Rotates format based on week number."""
    results = _load_results()
    if not results:
        print("  No results data for weekly audit.")
        return

    season = _season_stats(results)
    week = _week_stats(results)

    week_num = datetime.now().isocalendar()[1]
    fmt = week_num % 3

    if fmt == 0 and week:
        tweet = _format_audit(week, season)
        label = "Tout Audit"
    elif fmt == 1:
        tweet = _format_math(season)
        label = "Math vs Vibes"
    else:
        tweet = _format_ask_capper(season)
        label = "Ask Your Capper"

    if len(tweet) > 280:
        tweet = tweet[:277] + "..."

    print(f"  Weekly audit ({label}, {len(tweet)} chars):")
    print(f"  ---")
    for line in tweet.split("\n"):
        print(f"  {line}")
    print(f"  ---")

    client, _ = _get_clients()
    if not client:
        return

    try:
        response = client.create_tweet(text=tweet)
        tweet_id = response.data["id"]
        print(f"  Posted: https://x.com/Ozzy_Analytics/status/{tweet_id}")
    except Exception as e:
        print(f"  ERROR posting weekly audit: {e}")
