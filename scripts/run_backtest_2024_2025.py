"""
Run rolling backtests for 2024 and 2025 with a rolling bankroll.
Starts with $10K in 2024, rolls ending bankroll into 2025.
Outputs CSVs for the website backtest page.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR
from src.backtest.runner import run_rolling_backtest

STARTING_BANKROLL = 10_000.0

def main():
    # --- 2024 ---
    print("\n" + "=" * 60)
    print("  PHASE 1: 2024 Rolling Backtest ($10K starting)")
    print("=" * 60)

    out_2024 = DATA_DIR / "backtest_rolling_2024_weather.csv"
    df_2024 = run_rolling_backtest(
        start_year=2024,
        end_year=2024,
        output_path=out_2024,
        bankroll=STARTING_BANKROLL,
        rolling_bankroll=True,
    )

    ending_2024 = df_2024.attrs.get("ending_bankroll", STARTING_BANKROLL)
    print(f"\n  2024 ending bankroll: ${ending_2024:,.2f}")

    # --- 2025 ---
    print("\n" + "=" * 60)
    print(f"  PHASE 2: 2025 Rolling Backtest (${ending_2024:,.2f} starting)")
    print("=" * 60)

    out_2025 = DATA_DIR / "backtest_rolling_2025_weather.csv"
    df_2025 = run_rolling_backtest(
        start_year=2025,
        end_year=2025,
        output_path=out_2025,
        bankroll=ending_2024,
        rolling_bankroll=True,
    )

    ending_2025 = df_2025.attrs.get("ending_bankroll", ending_2024)
    print(f"\n  2025 ending bankroll: ${ending_2025:,.2f}")

    # --- Summary ---
    print("\n" + "=" * 60)
    print("  FINAL SUMMARY")
    print("=" * 60)
    print(f"  Starting bankroll:    ${STARTING_BANKROLL:,.2f}")
    print(f"  After 2024:           ${ending_2024:,.2f} ({(ending_2024/STARTING_BANKROLL - 1)*100:+.1f}%)")
    print(f"  After 2025:           ${ending_2025:,.2f} ({(ending_2025/STARTING_BANKROLL - 1)*100:+.1f}%)")
    total_return = (ending_2025 / STARTING_BANKROLL - 1) * 100
    print(f"  Total return:         {total_return:+.1f}%")


if __name__ == "__main__":
    main()
