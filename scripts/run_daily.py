"""
Daily pipeline: fetch today's odds, run simulations, output bet recommendations.

Usage:
    python scripts/run_daily.py --bankroll 1000
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from config import MIN_EDGE, KELLY_FRACTION, MAX_BET_FRACTION
from src.betting.odds import fetch_mlb_odds, parse_odds_response, american_to_decimal
from src.betting.kelly import size_bet


def run_daily(bankroll: float = 1000.0):
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'=' * 60}")
    print(f"  MLB Betting Model — {today}")
    print(f"  Bankroll: ${bankroll:,.2f}")
    print(f"{'=' * 60}")

    # 1. Fetch odds
    print("\nFetching moneyline odds...")
    try:
        raw  = fetch_mlb_odds(markets="h2h")
        odds = parse_odds_response(raw)
        print(f"  {len(odds)} games found\n")
    except Exception as e:
        print(f"  Error fetching odds: {e}")
        return

    if odds.empty:
        print("No games today.")
        return

    print(odds[["away_team", "home_team", "best_away_odds", "best_home_odds", "vig"]]
          .to_string(index=False))

    # 2. Simulate each game  (TODO: plug in real lineups + player profiles)
    print("\n" + "-" * 60)
    print("  Simulation step requires lineup data integration.")
    print("  Next milestone: connect lineup scraper -> run_daily pipeline.")
    print("-" * 60)

    # 3. Placeholder: once simulation is wired up, the flow will be:
    #    for each game:
    #        sim_result = monte_carlo_win_probability(...)
    #        edges = find_edges(sim_result, odds, home, away)
    #        for edge in edges:
    #            bet = size_bet(edge["model_prob"], edge["decimal_odds"], bankroll)
    #            print recommendation


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--bankroll", type=float, default=1000.0)
    args = parser.parse_args()
    run_daily(bankroll=args.bankroll)
