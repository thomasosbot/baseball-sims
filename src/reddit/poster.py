"""
Post daily picks to Reddit via PRAW.

Posts to r/sportsbook daily threads and optionally to your own subreddit.

Usage:
    from src.reddit.poster import post_daily_picks
    post_daily_picks(picks_data)

Requires in .env:
    REDDIT_CLIENT_ID
    REDDIT_CLIENT_SECRET
    REDDIT_USERNAME
    REDDIT_PASSWORD
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"

# Subreddits with daily pick threads
DAILY_THREAD_SUBS = ["sportsbook"]
# Your own subreddit (optional — set in .env)
OWN_SUBREDDIT_ENV = "REDDIT_SUBREDDIT"


def _get_reddit():
    """Create an authenticated PRAW Reddit instance."""
    try:
        import praw
    except ImportError:
        print("  ERROR: praw not installed. Run: pip install praw")
        return None

    client_id = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    username = os.getenv("REDDIT_USERNAME", "")
    password = os.getenv("REDDIT_PASSWORD", "")

    if not all([client_id, client_secret, username, password]):
        print("  ERROR: Reddit API credentials not set in .env")
        return None

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent="OzzyAnalytics/1.0 (by /u/" + username + ")",
    )


def _load_yesterday_results() -> dict | None:
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    return results[-1] if results else None


def _load_season_stats() -> dict | None:
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


def format_comment(picks_data: dict) -> str:
    """Format picks as a Reddit comment for daily threads."""
    date = picks_data.get("date", "")
    picks = picks_data.get("picks", [])

    lines = [f"**Ozzy Analytics MLB Model — {date}**", ""]

    # Yesterday's recap
    yesterday = _load_yesterday_results()
    if yesterday and yesterday.get("picks"):
        wins = yesterday.get("wins", 0)
        losses = yesterday.get("losses", 0)
        profit = yesterday.get("day_profit", 0)
        sign = "+" if profit >= 0 else ""
        lines.append(f"*Yesterday: {wins}-{losses} ({sign}${profit:.0f})*")
        for p in yesterday["picks"]:
            result = "\u2705" if p.get("won") else "\u274c"
            score = p.get("actual_score", "")
            pnl = p.get("profit", 0)
            ps = "+" if pnl >= 0 else ""
            lines.append(f"- {result} {p['pick']} ({score}) {ps}${pnl:.0f}")
        lines.append("")

    # Today's picks
    if picks:
        lines.append("**Today's Picks:**")
        lines.append("")
        lines.append("| Pick | Odds | Model Win% | Edge |")
        lines.append("|------|------|-----------|------|")
        for p in picks:
            odds = p.get("odds", "")
            prob = p.get("model_prob", 0)
            edge = p.get("edge_pct", 0)
            lines.append(f"| {p['pick']} | {odds} | {prob:.0%} | +{edge:.1f}% |")
        lines.append("")
    else:
        lines.append("No edges today. Model is sitting tight.")
        lines.append("")

    # Season stats
    stats = _load_season_stats()
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        sign = "+" if profit >= 0 else ""
        lines.append(f"**Season:** {w}-{l} | {sign}${profit:.0f} | {stats['roi']}% ROI")
        lines.append("")

    lines.append("*10,000 Monte Carlo simulations per game. Full analysis at [ozzyanalytics.com](https://ozzyanalytics.com)*")
    lines.append("")
    lines.append("---")
    lines.append("*BOT | [Ozzy Analytics](https://ozzyanalytics.com)*")

    return "\n".join(lines)


def format_post(picks_data: dict) -> tuple[str, str]:
    """Format picks as a Reddit post (title, body) for own subreddit."""
    date = picks_data.get("date", "")
    picks = picks_data.get("picks", [])
    num = len(picks)

    title = f"MLB Picks — {date} ({num} pick{'s' if num != 1 else ''})"
    body = format_comment(picks_data)

    return title, body


def _find_daily_thread(reddit, subreddit_name: str, date: str):
    """Find today's daily picks thread in a subreddit."""
    sub = reddit.subreddit(subreddit_name)

    # r/sportsbook uses "MLB Daily - M/D/YY" format
    # Search hot posts for today's thread
    month = int(date[5:7])
    day = int(date[8:10])
    year = date[2:4]
    search_terms = [
        f"MLB Daily - {month}/{day}/{year}",
        f"MLB Daily Discussion - {month}/{day}",
        f"MLB Daily",
    ]

    for post in sub.hot(limit=25):
        title_lower = post.title.lower()
        if "mlb" in title_lower and "daily" in title_lower:
            # Check if it's today's thread (posted today)
            from datetime import datetime, timezone
            post_date = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
            if post_date.strftime("%Y-%m-%d") == date:
                return post

    # Fallback: search
    for term in search_terms:
        results = sub.search(term, sort="new", time_filter="day", limit=5)
        for post in results:
            return post

    return None


def post_daily_picks(picks_data: dict):
    """Post today's picks to Reddit."""
    reddit = _get_reddit()
    if not reddit:
        return

    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    comment_text = format_comment(picks_data)

    # Post to daily threads
    for sub_name in DAILY_THREAD_SUBS:
        print(f"  Looking for daily thread in r/{sub_name}...")
        thread = _find_daily_thread(reddit, sub_name, date)
        if thread:
            try:
                thread.reply(comment_text)
                print(f"  Posted comment to r/{sub_name}: {thread.title}")
            except Exception as e:
                print(f"  ERROR posting to r/{sub_name}: {e}")
        else:
            print(f"  No daily thread found in r/{sub_name} for {date}")

    # Post to own subreddit (if configured)
    own_sub = os.getenv(OWN_SUBREDDIT_ENV, "")
    if own_sub:
        title, body = format_post(picks_data)
        try:
            sub = reddit.subreddit(own_sub)
            submission = sub.submit(title, selftext=body)
            print(f"  Posted to r/{own_sub}: {submission.url}")
        except Exception as e:
            print(f"  ERROR posting to r/{own_sub}: {e}")
