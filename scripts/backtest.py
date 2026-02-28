"""
Run the historical backtest and print a performance summary.

Usage:
    python scripts/backtest.py                     # full backtest 2021-2024
    python scripts/backtest.py --quick             # 50 games/year smoke test
    python scripts/backtest.py --years 2023 2024   # specific years
    python scripts/backtest.py --bankroll 10000    # set starting bankroll
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import BACKTEST_START_YEAR, BACKTEST_END_YEAR, PROCESSED_DIR
from src.backtest.runner import run_backtest, run_rolling_backtest
from src.backtest.metrics import summarize_backtest, calibration_table


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run MLB model backtest")
    parser.add_argument("--years", nargs="+", type=int, default=None)
    parser.add_argument("--quick", action="store_true", help="50 games/year smoke test")
    parser.add_argument("--sims", type=int, default=1000, help="Simulations per game")
    parser.add_argument("--bankroll", type=float, default=10000, help="Starting bankroll ($)")
    parser.add_argument("--rolling", action="store_true", help="Use rolling-window profiles (no look-ahead)")
    parser.add_argument("--bat-regression", type=float, default=None,
                        help="Override BATTER_REGRESSION_SCALE")
    parser.add_argument("--pit-regression", type=float, default=None,
                        help="Override PITCHER_REGRESSION_SCALE")
    args = parser.parse_args()

    if args.bat_regression is not None:
        import config
        config.BATTER_REGRESSION_SCALE = args.bat_regression
        print(f"  Batter regression scale override: {args.bat_regression}")
    if args.pit_regression is not None:
        import config
        config.PITCHER_REGRESSION_SCALE = args.pit_regression
        print(f"  Pitcher regression scale override: {args.pit_regression}")

    start = args.years[0] if args.years else BACKTEST_START_YEAR
    end   = args.years[-1] if args.years else BACKTEST_END_YEAR
    max_g = 50 if args.quick else None

    output = PROCESSED_DIR / ("backtest_rolling.csv" if args.rolling else "backtest_results.csv")

    runner = run_rolling_backtest if args.rolling else run_backtest
    results = runner(
        start_year=start,
        end_year=end,
        n_sims=args.sims,
        max_games_per_year=max_g,
        output_path=output,
        bankroll=args.bankroll,
    )

    if results.empty:
        print("\nNo results.  Make sure data is fetched (python scripts/fetch_data.py).")
        return

    # ---------- Model accuracy ----------
    print(f"\n{'=' * 60}")
    print("  MODEL ACCURACY")
    print(f"{'=' * 60}")

    # Map our column names to what summarize_backtest expects
    r = results.copy()
    if "model_home_win_prob" in r.columns and "actual_home_win" in r.columns:
        r["model_prob"] = r["model_home_win_prob"]
        r["won"] = r["actual_home_win"].astype(bool)

    summary = summarize_backtest(r)
    for k, v in summary.items():
        if v is not None:
            if isinstance(v, float):
                print(f"  {k:20s}: {v:.4f}")
            else:
                print(f"  {k:20s}: {v}")

    if {"model_home_win_prob", "actual_home_win"} <= set(results.columns):
        print(f"\n{'=' * 60}")
        print("  CALIBRATION TABLE")
        print(f"{'=' * 60}")
        cal = calibration_table(
            results["model_home_win_prob"].tolist(),
            results["actual_home_win"].astype(int).tolist(),
        )
        print(cal.to_string(index=False))

    # ---------- Betting performance ----------
    if "bet_side" in results.columns:
        bets = results[results["bet_side"].notna()].copy()
        total_games = len(results)
        odds_matched = results["market_home_nv_prob"].notna().sum() if "market_home_nv_prob" in results.columns else 0

        print(f"\n{'=' * 60}")
        print("  BETTING PERFORMANCE")
        print(f"{'=' * 60}")
        print(f"  {'Games simulated':25s}: {total_games}")
        print(f"  {'Games matched to odds':25s}: {odds_matched}")
        print(f"  {'Bets placed':25s}: {len(bets)}")

        if not bets.empty:
            total_staked = bets["bet_stake"].sum()
            total_profit = bets["bet_profit"].sum()
            bet_roi = (total_profit / total_staked * 100) if total_staked else 0
            win_rate = bets["bet_won"].mean() * 100
            avg_edge = bets["bet_edge"].mean() * 100
            avg_odds = bets["bet_odds"].mean()

            print(f"  {'Bet win rate':25s}: {win_rate:.1f}%")
            print(f"  {'Avg edge':25s}: {avg_edge:.1f}%")
            print(f"  {'Avg odds (American)':25s}: {avg_odds:+.0f}")
            print(f"  {'Total staked':25s}: ${total_staked:,.2f}")
            print(f"  {'Total profit':25s}: ${total_profit:,.2f}")
            print(f"  {'ROI':25s}: {bet_roi:+.1f}%")
            print(f"  {'Starting bankroll':25s}: ${args.bankroll:,.2f}")
            print(f"  {'Ending bankroll':25s}: ${args.bankroll + total_profit:,.2f}")

            # Home vs away breakdown
            home_bets = bets[bets["bet_side"] == "home"]
            away_bets = bets[bets["bet_side"] == "away"]
            print(f"\n  {'Home bets':25s}: {len(home_bets)} ({home_bets['bet_won'].mean()*100:.0f}% W)")
            print(f"  {'Away bets':25s}: {len(away_bets)} ({away_bets['bet_won'].mean()*100:.0f}% W)")

            # Best and worst bets
            if len(bets) >= 3:
                best = bets.nlargest(3, "bet_profit")
                worst = bets.nsmallest(3, "bet_profit")
                print(f"\n  Top 3 wins:")
                for _, b in best.iterrows():
                    side_team = b["home_team"] if b["bet_side"] == "home" else b["away_team"]
                    print(f"    {b['date'][:10]}  {side_team:4s} ({b['bet_odds']:+.0f})  +${b['bet_profit']:.2f}")
                print(f"  Worst 3 losses:")
                for _, b in worst.iterrows():
                    side_team = b["home_team"] if b["bet_side"] == "home" else b["away_team"]
                    print(f"    {b['date'][:10]}  {side_team:4s} ({b['bet_odds']:+.0f})  ${b['bet_profit']:.2f}")
        else:
            print("  No bets exceeded the minimum edge threshold.")


if __name__ == "__main__":
    main()
