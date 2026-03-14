"""
One-time preseason setup: build Marcel+BHQ projections, init Elo, save state.

Usage:
    python scripts/init_season.py              # defaults to SEASON_YEAR from config
    python scripts/init_season.py --year 2026
    python scripts/init_season.py --bankroll 10000
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SEASON_YEAR
from src.data.state import init_preseason, save_state


def main(year: int = SEASON_YEAR, bankroll: float = 10_000.0):
    print(f"\n{'=' * 60}")
    print(f"  Preseason Initialization — {year}")
    print(f"  Starting bankroll: ${bankroll:,.2f}")
    print(f"{'=' * 60}\n")

    cumulative, elo, batter_speeds = init_preseason(year)

    # Save as "preseason" state (date = opening day placeholder)
    save_state(cumulative, elo, batter_speeds, bankroll, date=f"{year}-03-01")

    print(f"\n  Done! State saved. Ready for daily pipeline.")
    print(f"  Run: python scripts/run_daily.py --bankroll {bankroll:.0f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Initialize preseason state")
    parser.add_argument("--year", type=int, default=SEASON_YEAR)
    parser.add_argument("--bankroll", type=float, default=10_000.0)
    args = parser.parse_args()
    main(year=args.year, bankroll=args.bankroll)
