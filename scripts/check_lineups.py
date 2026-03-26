"""
Lightweight lineup watcher: checks if new lineups have been confirmed
since the last pipeline run. Exits with code 0 if changes detected
(should trigger full pipeline), code 1 if no changes.

Usage:
    python scripts/check_lineups.py                 # check today
    python scripts/check_lineups.py --date 2026-04-01
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import statsapi

from config import DATA_DIR
from src.data.fetch import team_abbrev

DAILY_DIR = DATA_DIR / "daily"


def check_lineups(date: str = None) -> dict:
    """
    Compare confirmed lineups from MLB API against the last pipeline run.

    Returns dict with:
        changed: bool — True if any game gained confirmed lineups
        new_confirmations: list of matchup strings that are newly confirmed
        total_confirmed: int — how many games now have confirmed lineups
        total_games: int — total games on the schedule
        prev_run_mode: str — the mode of the last pipeline run
    """
    today = date or datetime.now().strftime("%Y-%m-%d")
    daily_file = DAILY_DIR / f"{today}.json"

    # Load previous run's state
    prev_statuses = {}
    prev_run_mode = None
    if daily_file.exists():
        with open(daily_file) as f:
            prev = json.load(f)
        prev_run_mode = prev.get("run_mode")
        for g in prev.get("games", []):
            key = f"{g['away']}@{g['home']}"
            prev_statuses[key] = g.get("lineup_status", "pending")

    # Fetch current lineup status from MLB API (lightweight — no boxscores)
    m, d, y = today[5:7], today[8:10], today[:4]
    try:
        schedule = statsapi.schedule(date=f"{m}/{d}/{y}")
    except Exception as e:
        print(f"Error fetching schedule: {e}")
        return {"changed": False, "new_confirmations": [], "total_confirmed": 0, "total_games": 0}

    games = [g for g in schedule if g.get("game_type") == "R"]
    if not games:
        print("No regular season games today.")
        return {"changed": False, "new_confirmations": [], "total_confirmed": 0, "total_games": 0}

    # Check each game for confirmed lineups
    confirmed_now = 0
    new_confirmations = []
    total_games = len(games)

    for g in games:
        home = team_abbrev(g["home_name"])
        away = team_abbrev(g["away_name"])
        key = f"{away}@{home}"

        # Try to fetch lineup — if both sides have 9 batters, it's confirmed
        try:
            from src.data.fetch import fetch_game_lineup
            lineups = fetch_game_lineup(g["game_id"])
            home_count = len(lineups.get("home", []))
            away_count = len(lineups.get("away", []))
        except Exception:
            home_count = 0
            away_count = 0

        is_confirmed = home_count >= 9 and away_count >= 9

        if is_confirmed:
            confirmed_now += 1
            prev_status = prev_statuses.get(key, "pending")
            if prev_status in ("pending", "projected"):
                new_confirmations.append(key)

        import time
        time.sleep(0.15)  # gentle rate limit

    changed = len(new_confirmations) > 0
    return {
        "changed": changed,
        "new_confirmations": new_confirmations,
        "total_confirmed": confirmed_now,
        "total_games": total_games,
        "prev_run_mode": prev_run_mode,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Check for newly confirmed lineups")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()

    result = check_lineups(date=args.date)

    print(f"\nLineup Check — {args.date or datetime.now().strftime('%Y-%m-%d')}")
    print(f"  Games: {result['total_confirmed']}/{result['total_games']} confirmed")
    print(f"  Previous run: {result['prev_run_mode'] or 'none'}")

    if result["new_confirmations"]:
        print(f"  NEW confirmations: {', '.join(result['new_confirmations'])}")
    else:
        print(f"  No new confirmations since last run.")

    # Exit code: 0 = changes detected (trigger pipeline), 1 = no changes
    sys.exit(0 if result["changed"] else 1)
