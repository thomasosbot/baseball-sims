"""
Populate the data cache.

Usage:
    python scripts/fetch_data.py                    # fetch 2024 (most recent full season)
    python scripts/fetch_data.py --years 2023 2024  # specific years
    python scripts/fetch_data.py --statcast         # include full Statcast (slow, ~10 min/year)
    python scripts/fetch_data.py --odds             # fetch historical odds (~180 API calls/year)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.fetch import (
    fetch_statcast_season,
    fetch_season_schedule,
    fetch_season_historical_odds,
    build_closing_lines,
    build_closing_totals,
)
from config import CACHE_DIR


def fetch_all(years: list, include_statcast: bool = False, include_odds: bool = False):
    for year in years:
        print(f"\n{'='*40}")
        print(f"  {year}")
        print(f"{'='*40}")

        print("  Schedule + game results...")
        sched = fetch_season_schedule(year)
        print(f"    {len(sched)} games")

        if include_statcast:
            print("  Statcast pitch-level data...")
            sc = fetch_statcast_season(year)
            print(f"    {len(sc):,} pitches")
        else:
            print("  Skipping Statcast (use --statcast to include)")

        if include_odds:
            print("  Historical odds (h2h + totals)...")
            odds_df = fetch_season_historical_odds(year, include_totals=True)
            print(f"    {len(odds_df):,} raw h2h odds rows")

            if not odds_df.empty:
                closing = build_closing_lines(odds_df)
                closing_path = CACHE_DIR / f"closing_lines_{year}.pkl"
                closing.to_pickle(closing_path)
                print(f"    {len(closing)} games with closing lines → {closing_path.name}")

            # Build totals closing lines if available
            totals_cache = CACHE_DIR / f"historical_totals_{year}.pkl"
            if totals_cache.exists():
                import pandas as pd
                totals_df = pd.read_pickle(totals_cache)
                print(f"    {len(totals_df):,} raw totals odds rows")
                if not totals_df.empty:
                    closing_totals = build_closing_totals(totals_df)
                    totals_path = CACHE_DIR / f"closing_totals_{year}.pkl"
                    closing_totals.to_pickle(totals_path)
                    print(f"    {len(closing_totals)} games with closing totals → {totals_path.name}")
        else:
            print("  Skipping odds (use --odds to include)")

    print("\nDone.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Pre-fetch MLB data into local cache")
    parser.add_argument(
        "--years", nargs="+", type=int, default=[2024],
        help="Seasons to fetch (default: 2024)",
    )
    parser.add_argument(
        "--statcast", action="store_true",
        help="Also fetch full Statcast data (~10 min per year)",
    )
    parser.add_argument(
        "--odds", action="store_true",
        help="Fetch historical odds from The Odds API (~180 requests per year)",
    )
    args = parser.parse_args()
    fetch_all(args.years, include_statcast=args.statcast, include_odds=args.odds)
