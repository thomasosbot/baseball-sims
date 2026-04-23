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

from src.betting.units import fmt_u, fmt_ud

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
        lines = [f"**{wins}W-{losses}L** ({fmt_ud(profit, signed=True)})"]
        for p in yesterday["picks"]:
            emoji = "\u2705" if p.get("won") else "\u274c"
            pnl = p.get("profit", 0)
            lines.append(f"{emoji} {p['pick']} — {p.get('actual_score', '')} ({fmt_ud(pnl, signed=True)})")
        recap_field = {
            "name": f"\U0001f4ca Yesterday ({yesterday['date']})",
            "value": "\n".join(lines),
            "inline": False,
        }

    # Today's picks \u2014 one field per pick (header + narrative)
    pick_fields = []
    for p in picks:
        odds = p.get("odds", "")
        prob = p.get("model_prob", 0)
        edge = p.get("edge_pct", 0)
        team = p.get("team", "")
        opponent = p.get("opponent", "")
        side = p.get("side", "")
        matchup = f"{team} @ {opponent}" if side == "away" else f"{opponent} @ {team}"
        wager = abs(p.get("wager", 0))
        bet_line = f"\u2003{fmt_ud(wager)} wager" if wager else ""
        narrative = p.get("explanation", "").strip()
        # Discord field value cap is 1024 chars
        header = f"\u2003{matchup}{bet_line}"
        body = f"{header}\n\n{narrative}" if narrative else header
        if len(body) > 1020:
            body = body[:1017] + "..."
        pick_fields.append({
            "name": f"{p['pick']} ({odds}) | {prob:.0%} win | +{edge:.1f}% edge",
            "value": body,
            "inline": False,
        })

    picks_value = "No edges found today. The model is sitting tight." if not pick_fields else None

    # Season stats
    stats = _load_season_stats()
    footer_text = ""
    if stats:
        w, l = stats["wins"], stats["losses"]
        profit = stats["total_profit"]
        ps = "+" if profit >= 0 else "-"
        footer_text = (
            f"Season: {w}-{l} | {fmt_u(profit, signed=True)} "
            f"({ps}${abs(profit):,.0f}) | {stats['roi']}% ROI | "
            f"Bankroll: {fmt_u(stats['bankroll'])} (${stats['bankroll']:,.0f})"
        )

    # Build embed
    embed = {
        "title": f"\u26be Today's Picks — {date}",
        "color": 0x1877F2,  # accent blue
        "footer": {"text": footer_text or "ozzyanalytics.com"},
        "url": "https://ozzyanalytics.com",
    }
    if picks_value:
        embed["description"] = picks_value

    fields = []
    if recap_field:
        fields.append(recap_field)
    fields.extend(pick_fields)
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


def format_results_embed(day_results: dict) -> dict:
    """Build a Discord embed for nightly results."""
    date = day_results.get("date", "")
    picks = day_results.get("picks", [])
    wins = day_results.get("wins", 0)
    losses = day_results.get("losses", 0)
    profit = day_results.get("day_profit", 0)

    # Individual results
    lines = []
    for p in picks:
        emoji = "\u2705" if p.get("won") else "\u274c"
        pnl = p.get("profit", 0)
        wager = abs(p.get("wager", 0))
        bet_str = f" — Bet {fmt_ud(wager)}" if wager else ""
        lines.append(
            f"{emoji} **{p['pick']}** ({p.get('odds', '')}) — {p.get('actual_score', '')} "
            f"({fmt_ud(pnl, signed=True)}){bet_str}"
        )

    results_text = "\n".join(lines) if lines else "No picks today."

    # Season stats
    stats = _load_season_stats()
    footer_text = ""
    if stats:
        w, l = stats["wins"], stats["losses"]
        sp = stats["total_profit"]
        ps = "+" if sp >= 0 else "-"
        footer_text = (
            f"Season: {w}-{l} | {fmt_u(sp, signed=True)} "
            f"({ps}${abs(sp):,.0f}) | {stats['roi']}% ROI | "
            f"100u \u2192 {fmt_u(stats['bankroll'])} "
            f"($10K \u2192 ${stats['bankroll']:,.0f})"
        )

    # Color: green for profit, red for loss
    color = 0x31A24C if profit >= 0 else 0xFA383E

    embed = {
        "title": f"\u26be Results — {date} — {wins}W-{losses}L ({fmt_ud(profit, signed=True)})",
        "description": results_text,
        "color": color,
        "footer": {"text": footer_text or "ozzyanalytics.com"},
        "url": "https://ozzyanalytics.com/results.html",
    }

    return embed


def post_nightly_results():
    """Post nightly results to Discord #results channel."""
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL_RESULTS", "")
    if not webhook_url:
        print("  ERROR: DISCORD_WEBHOOK_URL_RESULTS not set")
        return

    if not RESULTS_PATH.exists():
        print("  No results to post.")
        return
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        print("  No results to post.")
        return

    today = results[-1]
    if not today.get("picks"):
        print("  No picks to report.")
        return

    embed = format_results_embed(today)

    payload = {
        "username": "Ozzy Analytics",
        "embeds": [embed],
    }

    print(f"  Posting results to Discord...")
    try:
        resp = requests.post(webhook_url, json=payload)
        if resp.status_code in (200, 204):
            print(f"  Posted results to Discord!")
        else:
            print(f"  ERROR: Discord returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  ERROR posting to Discord: {e}")
