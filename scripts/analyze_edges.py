"""
Edge analysis tool — reads existing backtest_rolling.csv and simulates P&L
under different edge strategies without re-running Monte Carlo.

Usage:
    python scripts/analyze_edges.py
    python scripts/analyze_edges.py --csv data/processed/backtest_rolling.csv
    python scripts/analyze_edges.py --bankroll 10000
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.betting.odds import american_to_decimal, american_to_prob
from src.betting.edge import compute_game_confidence


def load_backtest(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    print(f"Loaded {len(df)} games from {csv_path}")
    return df


def simulate_bets(
    df: pd.DataFrame,
    alpha: float = 1.0,
    min_edge: float = 0.03,
    min_confidence: float = 0.0,
    bankroll: float = 10_000.0,
) -> dict:
    """
    Simulate moneyline P&L on the backtest CSV under given parameters.

    alpha: confidence shrinkage multiplier (1.0 = use raw confidence, 0.5 = halve it)
    min_edge: minimum edge to place a bet
    min_confidence: minimum confidence score to place a bet
    """
    required = ["model_home_win_prob", "market_home_nv_prob", "best_home_odds",
                "best_away_odds", "actual_home_win"]
    for col in required:
        if col not in df.columns:
            return {"error": f"Missing column: {col}"}

    total_profit = 0.0
    bets = 0
    wins = 0
    total_staked = 0.0

    for _, row in df.iterrows():
        model_home = row["model_home_win_prob"]
        model_away = row["model_away_win_prob"]
        market_home = row.get("market_home_nv_prob", np.nan)
        market_away = row.get("market_away_nv_prob", np.nan)

        if pd.isna(market_home) or pd.isna(market_away):
            continue

        # Compute confidence
        cum_pitchers = row.get("cumulative_pitchers", 1300)
        confidence = compute_game_confidence(
            cumulative_pitchers=int(cum_pitchers) if not pd.isna(cum_pitchers) else 1300,
            model_prob=model_home,
            market_prob=market_home,
        )
        effective_confidence = confidence * alpha

        if effective_confidence < min_confidence:
            continue

        # Shrink model prob toward market
        adj_home = market_home + effective_confidence * (model_home - market_home)
        adj_away = market_away + effective_confidence * (model_away - market_away)

        # Check home edge
        home_edge = adj_home - market_home
        home_odds = row["best_home_odds"]
        home_decimal = american_to_decimal(home_odds)
        home_ev = adj_home * (home_decimal - 1) - (1 - adj_home)

        # Check away edge
        away_edge = adj_away - market_away
        away_odds = row["best_away_odds"]
        away_decimal = american_to_decimal(away_odds)
        away_ev = adj_away * (away_decimal - 1) - (1 - adj_away)

        bet_side = None
        if home_edge >= min_edge and home_ev > 0:
            bet_side = "home"
            bet_prob = adj_home
            bet_decimal = home_decimal
            bet_won = bool(row["actual_home_win"])
        elif away_edge >= min_edge and away_ev > 0:
            bet_side = "away"
            bet_prob = adj_away
            bet_decimal = away_decimal
            bet_won = not bool(row["actual_home_win"])

        if bet_side is None:
            continue

        # Quarter Kelly sizing
        b = bet_decimal - 1
        kelly_full = max(0, (b * bet_prob - (1 - bet_prob)) / b) if b > 0 else 0
        kelly_adj = kelly_full * 0.25
        bet_frac = min(kelly_adj, 0.05)
        stake = bankroll * bet_frac

        if stake <= 0:
            continue

        profit = stake * (bet_decimal - 1) if bet_won else -stake
        total_profit += profit
        total_staked += stake
        bets += 1
        if bet_won:
            wins += 1

    return {
        "bets": bets,
        "wins": wins,
        "win_rate": wins / bets if bets > 0 else 0,
        "total_profit": round(total_profit, 2),
        "total_staked": round(total_staked, 2),
        "roi": total_profit / total_staked if total_staked > 0 else 0,
    }


def simulate_totals_bets(
    df: pd.DataFrame,
    alpha: float = 1.0,
    min_edge: float = 0.03,
    min_confidence: float = 0.0,
    bankroll: float = 10_000.0,
) -> dict:
    """Simulate totals (over/under) P&L."""
    required = ["model_over_prob", "market_over_nv_prob", "best_over_odds",
                "best_under_odds", "total_line", "actual_home_score", "actual_away_score"]
    for col in required:
        if col not in df.columns:
            return {"error": f"Missing column: {col}"}

    total_profit = 0.0
    bets = 0
    wins = 0
    pushes = 0
    total_staked = 0.0

    for _, row in df.iterrows():
        model_over = row.get("model_over_prob", np.nan)
        model_under = row.get("model_under_prob", np.nan)
        market_over = row.get("market_over_nv_prob", np.nan)
        market_under = row.get("market_under_nv_prob", np.nan)

        if pd.isna(model_over) or pd.isna(market_over):
            continue

        cum_pitchers = row.get("cumulative_pitchers", 1300)
        confidence = compute_game_confidence(
            cumulative_pitchers=int(cum_pitchers) if not pd.isna(cum_pitchers) else 1300,
            model_prob=model_over,
            market_prob=market_over,
        )
        effective_confidence = confidence * alpha

        if effective_confidence < min_confidence:
            continue

        adj_over = market_over + effective_confidence * (model_over - market_over)
        adj_under = market_under + effective_confidence * (model_under - market_under)

        over_odds = row["best_over_odds"]
        under_odds = row["best_under_odds"]

        # Skip garbage odds (zero, NaN, or |odds| < 100)
        if pd.isna(over_odds) or pd.isna(under_odds) or over_odds == 0 or under_odds == 0:
            continue
        if abs(over_odds) < 100 or abs(under_odds) < 100:
            continue

        over_edge = adj_over - market_over
        over_decimal = american_to_decimal(over_odds)
        over_ev = adj_over * (over_decimal - 1) - (1 - adj_over)

        under_edge = adj_under - market_under
        under_decimal = american_to_decimal(under_odds)
        under_ev = adj_under * (under_decimal - 1) - (1 - adj_under)

        bet_side = None
        if over_edge >= min_edge and over_ev > 0:
            bet_side = "over"
            bet_prob = adj_over
            bet_decimal = over_decimal
        elif under_edge >= min_edge and under_ev > 0:
            bet_side = "under"
            bet_prob = adj_under
            bet_decimal = under_decimal

        if bet_side is None:
            continue

        b = bet_decimal - 1
        kelly_full = max(0, (b * bet_prob - (1 - bet_prob)) / b) if b > 0 else 0
        kelly_adj = kelly_full * 0.25
        bet_frac = min(kelly_adj, 0.05)
        stake = bankroll * bet_frac

        if stake <= 0:
            continue

        actual_total = row["actual_home_score"] + row["actual_away_score"]
        line = row["total_line"]
        if actual_total == line:
            pushes += 1
            continue  # push

        if bet_side == "over":
            bet_won = actual_total > line
        else:
            bet_won = actual_total < line

        profit = stake * (bet_decimal - 1) if bet_won else -stake
        total_profit += profit
        total_staked += stake
        bets += 1
        if bet_won:
            wins += 1

    return {
        "bets": bets,
        "wins": wins,
        "pushes": pushes,
        "win_rate": wins / bets if bets > 0 else 0,
        "total_profit": round(total_profit, 2),
        "total_staked": round(total_staked, 2),
        "roi": total_profit / total_staked if total_staked > 0 else 0,
    }


def grid_search(df: pd.DataFrame, bankroll: float = 10_000.0):
    """Grid-search over alpha, min_edge, and min_confidence."""
    alphas = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    min_edges = [0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10]
    min_confs = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6]

    results = []
    for alpha in alphas:
        for me in min_edges:
            for mc in min_confs:
                r = simulate_bets(df, alpha=alpha, min_edge=me, min_confidence=mc, bankroll=bankroll)
                if "error" in r:
                    continue
                r["alpha"] = alpha
                r["min_edge"] = me
                r["min_confidence"] = mc
                results.append(r)

    rdf = pd.DataFrame(results)
    # Filter to combos with at least 50 bets
    rdf = rdf[rdf["bets"] >= 50]
    rdf = rdf.sort_values("roi", ascending=False)
    return rdf


def monthly_breakdown(df: pd.DataFrame, alpha: float, min_edge: float, min_confidence: float):
    """Show P&L by month for a given parameter set."""
    df = df.copy()
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M")

    months = sorted(df["month"].unique())
    print(f"\n{'Month':<10} {'Bets':>5} {'Wins':>5} {'Win%':>6} {'Profit':>10} {'ROI':>7}")
    print("-" * 50)

    for m in months:
        mdf = df[df["month"] == m]
        r = simulate_bets(mdf, alpha=alpha, min_edge=min_edge, min_confidence=min_confidence)
        if "error" in r or r["bets"] == 0:
            continue
        print(f"{str(m):<10} {r['bets']:>5} {r['wins']:>5} {r['win_rate']:>5.1%} "
              f"${r['total_profit']:>9,.0f} {r['roi']:>6.1%}")


def edge_bucket_breakdown(df: pd.DataFrame):
    """Show current-CSV bet outcomes by raw edge bucket."""
    bets = df[df["bet_side"].notna()].copy()
    if bets.empty:
        print("No bets found in CSV")
        return

    bins = [0, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20, 1.0]
    labels = ["0-3%", "3-5%", "5-7%", "7-10%", "10-15%", "15-20%", "20%+"]
    bets["edge_bucket"] = pd.cut(bets["bet_edge"], bins=bins, labels=labels, right=False)

    print(f"\n{'Edge Bucket':<12} {'Bets':>5} {'Wins':>5} {'Win%':>6} {'Avg Odds':>10} {'Profit':>10}")
    print("-" * 55)
    for bucket in labels:
        b = bets[bets["edge_bucket"] == bucket]
        if b.empty:
            continue
        wins = b["bet_won"].sum()
        n = len(b)
        profit = b["bet_profit"].sum()
        avg_odds = b["bet_odds"].mean()
        print(f"{bucket:<12} {n:>5} {int(wins):>5} {wins/n:>5.1%} "
              f"{avg_odds:>+10.0f} ${profit:>9,.0f}")


def main():
    parser = argparse.ArgumentParser(description="Analyze edge detection strategies")
    parser.add_argument("--csv", default="data/processed/backtest_rolling.csv",
                        help="Path to backtest CSV")
    parser.add_argument("--bankroll", type=float, default=10_000.0)
    parser.add_argument("--grid", action="store_true", help="Run full grid search")
    args = parser.parse_args()

    df = load_backtest(args.csv)

    # --- Current state ---
    print("\n" + "=" * 60)
    print("  CURRENT STATE (raw CSV bets)")
    print("=" * 60)

    edge_bucket_breakdown(df)

    bets = df[df["bet_side"].notna()]
    if not bets.empty:
        n = len(bets)
        wins = bets["bet_won"].sum()
        profit = bets["bet_profit"].sum()
        staked = bets["bet_stake"].sum()
        print(f"\nMoneyline: {n} bets, {wins/n:.1%} win rate, "
              f"${profit:,.0f} profit, {profit/staked:.1%} ROI")

    totals_bets = df[df["totals_bet_side"].notna()]
    if not totals_bets.empty:
        n = len(totals_bets)
        wins = totals_bets["totals_bet_won"].sum()
        profit = totals_bets["totals_bet_profit"].sum()
        staked = totals_bets["totals_bet_stake"].sum()
        print(f"Totals:    {n} bets, {wins/n:.1%} win rate, "
              f"${profit:,.0f} profit, {profit/staked:.1%} ROI")

    # --- Confidence diagnostics ---
    print("\n" + "=" * 60)
    print("  CONFIDENCE DIAGNOSTICS")
    print("=" * 60)

    has_pitchers = "cumulative_pitchers" in df.columns
    if has_pitchers:
        df["_confidence"] = df.apply(
            lambda r: compute_game_confidence(
                cumulative_pitchers=int(r["cumulative_pitchers"]) if not pd.isna(r.get("cumulative_pitchers", np.nan)) else 1300,
                model_prob=r["model_home_win_prob"],
                market_prob=r.get("market_home_nv_prob", 0.5) if not pd.isna(r.get("market_home_nv_prob", np.nan)) else 0.5,
            ), axis=1
        )
        print(f"Confidence: mean={df['_confidence'].mean():.3f}, "
              f"min={df['_confidence'].min():.3f}, max={df['_confidence'].max():.3f}")
        print(f"Games with confidence < 0.5: {(df['_confidence'] < 0.5).sum()}")
        print(f"Games with confidence < 0.3: {(df['_confidence'] < 0.3).sum()}")
    else:
        print("No cumulative_pitchers column — all confidence = 1.0")

    # --- Grid search ---
    if args.grid:
        print("\n" + "=" * 60)
        print("  GRID SEARCH (moneyline)")
        print("=" * 60)
        rdf = grid_search(df, bankroll=args.bankroll)
        print(f"\nTop 20 parameter combos (min 50 bets):")
        print(f"{'Alpha':>6} {'MinEdge':>8} {'MinConf':>8} {'Bets':>5} {'Win%':>6} "
              f"{'Profit':>10} {'ROI':>7}")
        print("-" * 58)
        for _, r in rdf.head(20).iterrows():
            print(f"{r['alpha']:>6.1f} {r['min_edge']:>8.2f} {r['min_confidence']:>8.1f} "
                  f"{int(r['bets']):>5} {r['win_rate']:>5.1%} "
                  f"${r['total_profit']:>9,.0f} {r['roi']:>6.1%}")

        # Also show totals grid for a few key combos
        print("\n" + "=" * 60)
        print("  TOTALS GRID (selected combos)")
        print("=" * 60)
        print(f"{'Alpha':>6} {'MinEdge':>8} {'MinConf':>8} {'Bets':>5} {'Win%':>6} "
              f"{'Profit':>10} {'ROI':>7}")
        print("-" * 58)
        for alpha in [0.3, 0.5, 0.7, 1.0]:
            for me in [0.03, 0.05, 0.07]:
                r = simulate_totals_bets(df, alpha=alpha, min_edge=me, bankroll=args.bankroll)
                if "error" in r or r["bets"] < 10:
                    continue
                print(f"{alpha:>6.1f} {me:>8.2f} {0.0:>8.1f} "
                      f"{r['bets']:>5} {r['win_rate']:>5.1%} "
                      f"${r['total_profit']:>9,.0f} {r['roi']:>6.1%}")
    else:
        # Quick comparison: baseline vs a few promising combos
        print("\n" + "=" * 60)
        print("  QUICK COMPARISON (moneyline)")
        print("=" * 60)
        combos = [
            (1.0, 0.03, 0.0, "Baseline (no shrinkage)"),
            (0.7, 0.03, 0.0, "alpha=0.7"),
            (0.5, 0.03, 0.0, "alpha=0.5"),
            (0.5, 0.05, 0.0, "alpha=0.5, edge=5%"),
            (0.5, 0.05, 0.3, "alpha=0.5, edge=5%, conf>0.3"),
            (0.7, 0.05, 0.3, "alpha=0.7, edge=5%, conf>0.3"),
            (1.0, 0.05, 0.0, "edge=5% only"),
            (1.0, 0.07, 0.0, "edge=7% only"),
        ]
        print(f"\n{'Strategy':<35} {'Bets':>5} {'Win%':>6} {'Profit':>10} {'ROI':>7}")
        print("-" * 68)
        for alpha, me, mc, label in combos:
            r = simulate_bets(df, alpha=alpha, min_edge=me, min_confidence=mc,
                              bankroll=args.bankroll)
            if "error" in r:
                continue
            print(f"{label:<35} {r['bets']:>5} {r['win_rate']:>5.1%} "
                  f"${r['total_profit']:>9,.0f} {r['roi']:>6.1%}")

        # Monthly breakdown for baseline
        print("\n" + "=" * 60)
        print("  MONTHLY BREAKDOWN (baseline)")
        print("=" * 60)
        monthly_breakdown(df, alpha=1.0, min_edge=0.03, min_confidence=0.0)

    print("\nTip: Run with --grid for full parameter sweep")


if __name__ == "__main__":
    main()
