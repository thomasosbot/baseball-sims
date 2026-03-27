"""
Fetch yesterday's scores, grade picks, update CumulativeStats + Elo, track P&L.

Usage:
    python scripts/update_results.py                    # yesterday
    python scripts/update_results.py --date 2026-04-01  # specific date
"""

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import statsapi

from config import DATA_DIR
from src.data.fetch import team_abbrev, TEAM_NAME_TO_ABBREV
from src.data.state import load_state, save_state
from src.data.process import prepare_for_rolling
from src.betting.odds import american_to_decimal

DAILY_DIR = DATA_DIR / "daily"
RESULTS_PATH = DAILY_DIR / "results.json"


def update_results(date: str = None, include_spring: bool = False):
    """Grade picks for a date, update state with actual results."""
    if date is None:
        date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"\n{'=' * 60}")
    print(f"  Updating Results — {date}")
    print(f"{'=' * 60}")

    # --- 1. Load state ---
    state = load_state()
    if state is None:
        print("  ERROR: No state found. Run init_season.py first.")
        return
    cumulative = state["cumulative"]
    elo = state["elo"]
    batter_speeds = state["batter_speeds"]
    bankroll = state["bankroll"]

    # --- 2. Fetch actual game results ---
    print(f"\nFetching results for {date}...")
    m, d, y = date[5:7], date[8:10], date[:4]
    games = statsapi.schedule(date=f"{m}/{d}/{y}")
    allowed_types = {"R", "S"} if include_spring else {"R"}
    final_games = [g for g in games if g.get("game_type") in allowed_types
                   and "Final" in str(g.get("status", ""))]

    if not final_games:
        print("  No final games found for this date.")
        return

    print(f"  {len(final_games)} completed games")

    # --- 3. Grade picks (if we have a picks file for this date) ---
    picks_path = DAILY_DIR / f"{date}.json"
    daily_results = []

    if picks_path.exists():
        with open(picks_path) as f:
            daily_data = json.load(f)

        picks = daily_data.get("picks", [])
        if picks:
            print(f"\n  Grading {len(picks)} picks...")
            for pick in picks:
                result = _grade_pick(pick, final_games)
                if result:
                    daily_results.append(result)
                    bankroll += result["profit"]
                    status = "W" if result["won"] else ("P" if result["profit"] == 0 else "L")
                    print(f"    {result['pick']:<25s} {status}  "
                          f"profit=${result['profit']:+,.2f}  "
                          f"bankroll=${bankroll:,.2f}")

            wins = sum(1 for r in daily_results if r["won"])
            losses = sum(1 for r in daily_results if not r["won"] and r["profit"] != 0)
            day_profit = sum(r["profit"] for r in daily_results)
            print(f"\n  Day: {wins}W-{losses}L  P&L: ${day_profit:+,.2f}  "
                  f"Bankroll: ${bankroll:,.2f}")
    else:
        print(f"  No picks file for {date}")

    # --- 4. Update Elo with actual results ---
    print(f"\n  Updating Elo ratings...")
    for g in final_games:
        home_abbr = team_abbrev(g["home_name"])
        away_abbr = team_abbrev(g["away_name"])
        home_score = g.get("home_score", 0) or 0
        away_score = g.get("away_score", 0) or 0
        if home_score != away_score:
            elo.update(home_abbr, away_abbr, home_score > away_score)

    # --- 5. Update CumulativeStats with actual PA data ---
    # For the daily pipeline, we fetch boxscore-level data from statsapi
    # rather than Statcast (which has a multi-day delay).
    # This gives us enough for profile updates.
    print(f"  Updating cumulative stats from boxscores...")
    pa_updated = 0
    for g in final_games:
        try:
            pa_updated += _update_cumulative_from_boxscore(g["game_id"], cumulative)
        except Exception as e:
            print(f"    Warning: could not process game {g['game_id']}: {e}")
        time.sleep(0.2)
    print(f"    {pa_updated} PAs ingested from {len(final_games)} games")

    # --- 6. Append to season results log ---
    season_results = []
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH) as f:
            season_results = json.load(f)

    day_entry = {
        "date": date,
        "games_played": len(final_games),
        "picks_count": len(daily_results),
        "wins": sum(1 for r in daily_results if r["won"]),
        "losses": sum(1 for r in daily_results if not r["won"] and r["profit"] != 0),
        "pushes": sum(1 for r in daily_results if r["profit"] == 0 and not r["won"]),
        "day_profit": round(sum(r["profit"] for r in daily_results), 2),
        "bankroll": round(bankroll, 2),
        "picks": daily_results,
    }
    # Replace existing entry for this date (avoid duplicates on re-runs)
    season_results = [r for r in season_results if r["date"] != date]
    season_results.append(day_entry)
    season_results.sort(key=lambda r: r["date"])

    with open(RESULTS_PATH, "w") as f:
        json.dump(season_results, f, indent=2, default=str)

    # --- 7. Save updated state ---
    save_state(cumulative, elo, batter_speeds, bankroll, date)
    print(f"\n  State updated and saved for {date}")

    return day_entry


def _grade_pick(pick, final_games):
    """Grade a single pick against actual results."""
    pick_type = pick.get("type", "moneyline")

    if pick_type == "moneyline":
        team = pick.get("team", "")
        side = pick.get("side", "")
        opponent = pick.get("opponent", "")

        for g in final_games:
            home_abbr = team_abbrev(g["home_name"])
            away_abbr = team_abbrev(g["away_name"])

            if (side == "home" and home_abbr == team) or \
               (side == "away" and away_abbr == team):
                home_score = g.get("home_score", 0) or 0
                away_score = g.get("away_score", 0) or 0
                if home_score == away_score:
                    continue  # skip ties/suspended

                if side == "home":
                    won = home_score > away_score
                else:
                    won = away_score > home_score

                odds_str = pick.get("odds", "+100")
                decimal_odds = american_to_decimal(int(odds_str.replace("+", "")))
                wager = pick.get("wager", 0)
                profit = wager * (decimal_odds - 1) if won else -wager

                return {
                    "pick": pick["pick"],
                    "type": "moneyline",
                    "won": won,
                    "wager": wager,
                    "profit": round(profit, 2),
                    "odds": odds_str,
                    "actual_score": f"{away_abbr} {away_score} - {home_abbr} {home_score}",
                }

    elif pick_type == "run_line":
        team = pick.get("team", "")
        side = pick.get("side", "")

        for g in final_games:
            home_abbr = team_abbrev(g["home_name"])
            away_abbr = team_abbrev(g["away_name"])

            if (side == "home" and home_abbr == team) or \
               (side == "away" and away_abbr == team):
                home_score = g.get("home_score", 0) or 0
                away_score = g.get("away_score", 0) or 0
                if home_score == away_score:
                    continue

                # Run line is +1.5 for the dog
                margin = home_score - away_score
                if side == "home":
                    won = (margin + 1.5) > 0  # home +1.5 covers
                else:
                    won = (-margin + 1.5) > 0  # away +1.5 covers

                odds_str = pick.get("odds", "+100")
                decimal_odds = american_to_decimal(int(odds_str.replace("+", "")))
                wager = pick.get("wager", 0)
                profit = wager * (decimal_odds - 1) if won else -wager

                return {
                    "pick": pick["pick"],
                    "type": "run_line",
                    "won": won,
                    "wager": wager,
                    "profit": round(profit, 2),
                    "odds": odds_str,
                    "actual_score": f"{away_abbr} {away_score} - {home_abbr} {home_score}",
                }

    elif pick_type == "totals":
        pick_str = pick.get("pick", "")
        # Parse "NYY@BOS OVER 8.5" format
        for g in final_games:
            home_abbr = team_abbrev(g["home_name"])
            away_abbr = team_abbrev(g["away_name"])
            if home_abbr in pick_str and away_abbr in pick_str:
                home_score = g.get("home_score", 0) or 0
                away_score = g.get("away_score", 0) or 0
                total = home_score + away_score

                is_over = "OVER" in pick_str
                # Extract line from pick string
                parts = pick_str.split()
                try:
                    line = float(parts[-1])
                except (ValueError, IndexError):
                    continue

                if total == line:
                    return {
                        "pick": pick["pick"], "type": "totals",
                        "won": False, "wager": pick.get("wager", 0),
                        "profit": 0.0, "odds": pick.get("odds", "+100"),
                        "actual_score": f"Total: {total}",
                    }

                won = (total > line) if is_over else (total < line)
                odds_str = pick.get("odds", "+100")
                decimal_odds = american_to_decimal(int(odds_str.replace("+", "")))
                wager = pick.get("wager", 0)
                profit = wager * (decimal_odds - 1) if won else -wager

                return {
                    "pick": pick["pick"], "type": "totals",
                    "won": won, "wager": wager,
                    "profit": round(profit, 2), "odds": odds_str,
                    "actual_score": f"Total: {total}",
                }

    return None


def _update_cumulative_from_boxscore(game_id, cumulative):
    """
    Update CumulativeStats from a boxscore.

    Since Statcast has a multi-day delay, we use the boxscore API for same-day
    updates. This gives us batting stats (AB, H, HR, BB, K, etc.) but not
    pitch-level Statcast data. Good enough for cumulative tracking.
    """
    box = statsapi.boxscore_data(game_id)
    pa_count = 0

    for side in ("home", "away"):
        # Extract team name from teamInfo
        team_info = box.get("teamInfo", {}).get(side, {})
        team_name = team_info.get("teamName", "") or team_info.get("shortName", "")
        team_abbr = team_abbrev(team_name) if team_name else ""

        batters = box.get(f"{side}Batters", [])
        for b in batters[1:]:  # skip header
            player_id = b.get("personId")
            if not player_id:
                continue

            # statsapi boxscore_data returns stats as flat string fields
            ab = int(b.get("ab", 0))
            hits = int(b.get("h", 0))
            hr = int(b.get("hr", 0))
            bb = int(b.get("bb", 0))
            k = int(b.get("k", 0))
            hbp = int(b.get("hbp", 0))
            doubles = int(b.get("doubles", 0))
            triples = int(b.get("triples", 0))
            singles = hits - hr - doubles - triples

            pa = ab + bb + hbp  # approximate PA (missing SF, SH)

            if pa == 0:
                continue

            # Update batter's cumulative PA outcome counts
            outs = ab - hits - k  # approximate
            outcomes = {
                "K": k, "BB": bb, "HBP": hbp, "HR": hr,
                "3B": triples, "2B": doubles, "1B": singles,
                "OUT": max(0, outs),
            }

            batter_data = cumulative._batters.get(player_id, {})
            for outcome, count in outcomes.items():
                batter_data[outcome] = batter_data.get(outcome, 0) + count
            batter_data["pa"] = batter_data.get("pa", 0) + pa
            cumulative._batters[player_id] = batter_data
            pa_count += pa

        # Update pitchers
        pitchers = box.get(f"{side}Pitchers", [])
        for i, p in enumerate(pitchers[1:]):  # skip header
            player_id = p.get("personId")
            if not player_id:
                continue

            # statsapi boxscore_data returns pitcher stats as flat string fields
            k = int(p.get("k", 0))
            bb = int(p.get("bb", 0))
            hr = int(p.get("hr", 0))
            hits = int(p.get("h", 0))
            doubles = int(p.get("doubles", 0))
            triples = int(p.get("triples", 0))
            singles = hits - hr - doubles - triples

            # Compute BF from IP + baserunners
            ip_str = str(p.get("ip", "0"))
            if "." in ip_str:
                whole, frac = ip_str.split(".")
                outs_recorded = int(whole) * 3 + int(frac)
            else:
                outs_recorded = int(float(ip_str)) * 3

            bf = outs_recorded + hits + bb + int(p.get("hbp", 0))
            if bf == 0:
                continue

            outs = max(0, bf - k - bb - int(p.get("hbp", 0)) - hits)

            outcomes = {
                "K": k, "BB": bb, "HBP": int(p.get("hbp", 0)), "HR": hr,
                "3B": triples, "2B": doubles, "1B": singles,
                "OUT": max(0, outs),
            }

            pitcher_data = cumulative._pitchers.get(player_id, {})
            for outcome, count in outcomes.items():
                pitcher_data[outcome] = pitcher_data.get(outcome, 0) + count
            pitcher_data["bf"] = pitcher_data.get("bf", 0) + bf
            cumulative._pitchers[player_id] = pitcher_data

            # Register relievers (not the first pitcher)
            if i > 0 and team_abbr:
                cumulative.register_reliever(player_id, team_abbr)

    return pa_count


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update results and grade picks")
    parser.add_argument("--date", type=str, default=None,
                        help="Date to grade (YYYY-MM-DD, default: yesterday)")
    parser.add_argument("--spring", action="store_true",
                        help="Include spring training games")
    args = parser.parse_args()
    update_results(date=args.date, include_spring=args.spring)
