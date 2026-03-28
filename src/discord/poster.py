"""
Post daily picks to Discord via webhook.

Usage:
    from src.discord.poster import post_daily_picks
    post_daily_picks(picks_data)

Requires DISCORD_WEBHOOK_URL in .env.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import requests

RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"


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


def format_embed(picks_data: dict) -> dict:
    """Build a Discord embed for daily picks."""
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks = picks_data.get("picks", [])

    # Yesterday's recap
    yesterday = _load_yesterday_results()
    recap_field = None
    if yesterday and yesterday.get("picks"):
        wins = yesterday.get("wins", 0)
        losses = yesterday.get("losses", 0)
        profit = yesterday.get("day_profit", 0)
        sign = "+" if profit >= 0 else ""
        lines = [f"**{wins}W-{losses}L** ({sign}${profit:.0f})"]
        for p in yesterday["picks"]:
            emoji = "\u2705" if p.get("won") else "\u274c"
            pnl = p.get("profit", 0)
            ps = "+" if pnl >= 0 else ""
            lines.append(f"{emoji} {p['pick']} — {p.get('actual_score', '')} ({ps}${pnl:.0f})")
        recap_field = {
            "name": f"\U0001f4ca Yesterday ({yesterday['date']})",
            "value": "\n".join(lines),
            "inline": False,
        }

    # Today's picks
    pick_lines = []
    for p in picks:
        pick_type = "ML" if p.get("type") == "moneyline" else "RL"
        odds = p.get("odds", "")
        prob = p.get("model_prob", 0)
        edge = p.get("edge_pct", 0)
        team = p.get("team", "")
        opponent = p.get("opponent", "")
        side = p.get("side", "")
        matchup = f"{team} @ {opponent}" if side == "away" else f"{opponent} @ {team}"
        pick_lines.append(
            f"**{p['pick']}** ({odds}) | {prob:.0%} win | +{edge:.1f}% edge\n"
            f"\u2003{matchup}"
        )

    picks_value = "\n\n".join(pick_lines) if pick_lines else "No edges found today. The model is sitting tight."

    # Season stats
    stats = _load_season_stats()
    footer_text = ""
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        sign = "+" if profit >= 0 else ""
        footer_text = f"Season: {w}-{l} | {sign}${profit:.0f} | {stats['roi']}% ROI | Bankroll: ${stats['bankroll']:,.0f}"

    # Build embed
    embed = {
        "title": f"\u26be Today's Picks — {date}",
        "description": picks_value,
        "color": 0x1877F2,  # accent blue
        "footer": {"text": footer_text or "ozzyanalytics.com"},
        "url": "https://ozzyanalytics.com",
    }

    fields = []
    if recap_field:
        fields.append(recap_field)
    if fields:
        embed["fields"] = fields

    return embed


def post_daily_picks(picks_data: dict):
    """Post today's picks to Discord via webhook."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("  ERROR: DISCORD_WEBHOOK_URL not set in .env")
        return

    embed = format_embed(picks_data)

    payload = {
        "username": "Ozzy Analytics",
        "embeds": [embed],
    }

    print(f"  Posting to Discord...")
    try:
        resp = requests.post(webhook_url, json=payload)
        if resp.status_code in (200, 204):
            print(f"  Posted to Discord!")
        else:
            print(f"  ERROR: Discord returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  ERROR posting to Discord: {e}")
