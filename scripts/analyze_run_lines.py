"""
Fetch 2025 run line (spread) odds and calculate hypothetical P&L.

Uses the existing 2025 backtest data + spread odds to determine if
run line bets would be profitable alongside our ML bets.

P(cover -1.5) is estimated from model win probability using the
empirical MLB relationship: ~70% of wins are by 2+ runs.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import time

from config import (
    CACHE_DIR, DATA_DIR, KELLY_FRACTION, MAX_BET_FRACTION,
    ML_ALPHA, ML_MIN_EDGE, ML_MAX_EDGE, ML_MIN_CONFIDENCE,
)
from src.data.fetch import (
    fetch_historical_odds_snapshot, fetch_season_schedule,
    CLOSING_LINE_BOOK, MAX_AMERICAN_ODDS, TEAM_NAME_TO_ABBREV,
    team_abbrev,
)
from src.betting.odds import american_to_prob, american_to_decimal, remove_vig
from src.betting.edge import calculate_edge
from src.betting.kelly import size_bet


# ---------------------------------------------------------------------------
# Step 1: Fetch spread odds for 2025
# ---------------------------------------------------------------------------

def fetch_spread_odds(year: int) -> pd.DataFrame:
    """Fetch historical spread (run line) odds for a season."""
    cache_path = CACHE_DIR / f"historical_spreads_{year}.pkl"
    if cache_path.exists():
        print(f"  Spreads cache exists: {cache_path}")
        return pd.read_pickle(cache_path)

    schedule = fetch_season_schedule(year)
    game_dates = sorted(schedule["game_date"].dropna().unique())
    print(f"  Fetching historical spread odds for {len(game_dates)} game days...")

    spread_rows = []
    for i, date_str in enumerate(game_dates):
        if isinstance(date_str, pd.Timestamp):
            d = date_str.strftime("%Y-%m-%d")
        else:
            d = str(date_str)[:10]

        iso = f"{d}T22:00:00Z"
        try:
            data, remaining = fetch_historical_odds_snapshot(iso, markets="spreads")
            games = data.get("data", [])

            for g in games:
                home = g["home_team"]
                away = g["away_team"]
                commence = g.get("commence_time", "")

                for bm in g.get("bookmakers", []):
                    book = bm["key"]
                    for mkt in bm.get("markets", []):
                        if mkt["key"] == "spreads":
                            home_price, away_price = None, None
                            home_point, away_point = None, None
                            for oc in mkt["outcomes"]:
                                if oc["name"] == home:
                                    home_price = oc["price"]
                                    home_point = oc.get("point")
                                elif oc["name"] == away:
                                    away_price = oc["price"]
                                    away_point = oc.get("point")

                            if (home_price is not None and away_price is not None
                                    and home_point is not None):
                                spread_rows.append({
                                    "game_date":     d,
                                    "commence_time": commence,
                                    "home_team":     home,
                                    "away_team":     away,
                                    "book":          book,
                                    "home_spread":   home_point,
                                    "away_spread":   away_point,
                                    "home_odds":     home_price,
                                    "away_odds":     away_price,
                                })

            if (i + 1) % 20 == 0:
                print(f"    {i+1}/{len(game_dates)} days fetched (remaining: {remaining})")

            time.sleep(0.3)
        except Exception as e:
            print(f"    {d}: error ({e})")
            time.sleep(1)

    df = pd.DataFrame(spread_rows)
    df.to_pickle(cache_path)
    print(f"  Cached {len(df)} spread odds rows across {df['game_date'].nunique()} days")
    return df


# ---------------------------------------------------------------------------
# Step 2: Build closing spread lines
# ---------------------------------------------------------------------------

def build_closing_spreads(spreads_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one closing spread line per game (FanDuel primary, median fallback).
    Standard MLB run line is ±1.5.
    """
    # Filter garbage: odds must be reasonable
    clean = spreads_df[
        (spreads_df["home_odds"].abs() <= MAX_AMERICAN_ODDS)
        & (spreads_df["away_odds"].abs() <= MAX_AMERICAN_ODDS)
        & (spreads_df["home_odds"].abs() >= 100)
        & (spreads_df["away_odds"].abs() >= 100)
    ].copy()

    # Only keep standard ±1.5 run lines
    clean = clean[
        ((clean["home_spread"] == -1.5) & (clean["away_spread"] == 1.5))
        | ((clean["home_spread"] == 1.5) & (clean["away_spread"] == -1.5))
    ].copy()

    # Compute implied probs and vig
    clean["_h_imp"] = clean["home_odds"].apply(american_to_prob)
    clean["_a_imp"] = clean["away_odds"].apply(american_to_prob)
    clean["_vig"] = clean["_h_imp"] + clean["_a_imp"] - 1.0

    # Filter to coherent lines: vig 0-12%
    clean = clean[(clean["_vig"] > -0.01) & (clean["_vig"] < 0.12)]

    records = []

    for (date, home, away), gdf in clean.groupby(["game_date", "home_team", "away_team"]):
        if len(gdf) < 3:
            continue

        # Determine which side is the favorite (-1.5)
        # Use the most common spread for home team
        home_spread = gdf["home_spread"].mode().iloc[0]

        fd = gdf[gdf["book"] == CLOSING_LINE_BOOK]
        if len(fd):
            row = fd.iloc[0]
            best_home_odds = row["home_odds"]
            best_away_odds = row["away_odds"]
            book_used = CLOSING_LINE_BOOK
        else:
            best_home_odds = gdf["home_odds"].median()
            best_away_odds = gdf["away_odds"].median()
            book_used = "consensus"

        # No-vig probabilities
        h_imp = american_to_prob(best_home_odds)
        a_imp = american_to_prob(best_away_odds)
        final_vig = h_imp + a_imp - 1.0
        if final_vig < -0.01 or final_vig > 0.12:
            continue
        h_nv, a_nv = remove_vig(h_imp, a_imp)

        records.append({
            "game_date":            date,
            "home_team_full":       home,
            "away_team_full":       away,
            "home_spread":          home_spread,
            "away_spread":          -home_spread,
            "best_home_spread_odds": best_home_odds,
            "best_away_spread_odds": best_away_odds,
            "spread_book":          book_used,
            "home_cover_nv_prob":   h_nv,
            "away_cover_nv_prob":   a_nv,
            "spread_vig":           final_vig,
            "n_books":              len(gdf),
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Step 3: Estimate P(cover ±1.5) from model win probability
# ---------------------------------------------------------------------------

def estimate_cover_prob(win_prob: float, spread: float) -> float:
    """
    Estimate P(cover spread) from model win probability.

    For -1.5 (favorite): P(cover) = P(win) * P(win by 2+ | win)
    For +1.5 (underdog): P(cover) = P(win) + P(lose by 1 | lose)

    MLB empiricals:
    - ~30% of decided games are decided by exactly 1 run
    - So P(win by 2+ | win) ≈ 0.70
    - P(lose by 1 | lose) ≈ 0.30
    """
    WIN_BY_2_PLUS_GIVEN_WIN = 0.70
    LOSE_BY_1_GIVEN_LOSE = 0.30

    if spread == -1.5:
        # Favorite: must win by 2+
        return win_prob * WIN_BY_2_PLUS_GIVEN_WIN
    elif spread == 1.5:
        # Underdog: win outright OR lose by 1
        return win_prob + (1.0 - win_prob) * LOSE_BY_1_GIVEN_LOSE
    else:
        return win_prob  # fallback


# ---------------------------------------------------------------------------
# Step 4: Run analysis
# ---------------------------------------------------------------------------

def main():
    year = 2025

    # Fetch spread odds
    print("=" * 60)
    print("  STEP 1: Fetch 2025 spread (run line) odds")
    print("=" * 60)
    spreads_raw = fetch_spread_odds(year)
    print(f"  Raw spread rows: {len(spreads_raw)}")

    # Build closing spreads
    print("\n" + "=" * 60)
    print("  STEP 2: Build closing spread lines")
    print("=" * 60)
    closing_spreads = build_closing_spreads(spreads_raw)
    print(f"  Closing spread lines: {len(closing_spreads)}")

    # Cache for reuse
    cache_path = CACHE_DIR / f"closing_spreads_{year}.pkl"
    closing_spreads.to_pickle(cache_path)
    print(f"  Cached to {cache_path}")

    # Load existing backtest data
    print("\n" + "=" * 60)
    print("  STEP 3: Load 2025 backtest data & calculate run line P&L")
    print("=" * 60)
    bt_path = DATA_DIR / "backtest_rolling_2025_weather.csv"
    bt = pd.read_csv(bt_path)
    print(f"  Loaded {len(bt)} backtest rows")

    # Match each backtest game to its spread line
    spread_bets = []
    matched = 0
    for _, row in bt.iterrows():
        date = str(row["date"])[:10]
        home_full = row.get("home_name", "")
        away_full = row.get("away_name", "")

        # Match to closing spread
        mask = (
            (closing_spreads["game_date"] == date)
            & (closing_spreads["home_team_full"] == home_full)
            & (closing_spreads["away_team_full"] == away_full)
        )
        matches = closing_spreads[mask]

        if matches.empty:
            # Fallback: abbreviation match
            home_abbrev = row.get("home_team", "")
            away_abbrev = row.get("away_team", "")
            day_games = closing_spreads[closing_spreads["game_date"] == date]
            found = False
            for _, srow in day_games.iterrows():
                if (TEAM_NAME_TO_ABBREV.get(srow["home_team_full"]) == home_abbrev
                        and TEAM_NAME_TO_ABBREV.get(srow["away_team_full"]) == away_abbrev):
                    matches = pd.DataFrame([srow])
                    found = True
                    break
            if not found:
                continue

        spread_row = matches.iloc[0]
        matched += 1

        home_spread = spread_row["home_spread"]
        away_spread = spread_row["away_spread"]
        home_spread_odds = spread_row["best_home_spread_odds"]
        away_spread_odds = spread_row["best_away_spread_odds"]
        home_cover_nv = spread_row["home_cover_nv_prob"]
        away_cover_nv = spread_row["away_cover_nv_prob"]

        model_home_wp = row["model_home_win_prob"]
        model_away_wp = row["model_away_win_prob"]
        confidence = row.get("confidence", 1.0)
        bankroll_val = row.get("bankroll", 10000)
        if pd.isna(bankroll_val):
            bankroll_val = 10000

        # Estimate cover probabilities
        model_home_cover = estimate_cover_prob(model_home_wp, home_spread)
        model_away_cover = estimate_cover_prob(model_away_wp, away_spread)

        # Calculate edges
        home_edge_info = calculate_edge(model_home_cover, home_cover_nv, home_spread_odds, confidence, alpha=ML_ALPHA)
        away_edge_info = calculate_edge(model_away_cover, away_cover_nv, away_spread_odds, confidence, alpha=ML_ALPHA)

        # Determine actual cover
        home_score = row.get("actual_home_score", 0)
        away_score = row.get("actual_away_score", 0)
        if pd.isna(home_score) or pd.isna(away_score):
            continue
        margin = home_score - away_score  # positive = home won by

        home_covered = margin + home_spread > 0  # e.g., margin > 1.5 if home_spread=-1.5
        away_covered = -margin + away_spread > 0  # e.g., -margin > 1.5 if away_spread=-1.5

        # Check if either side is a bet
        bet_info = {
            "game_id": row["game_id"],
            "date": date,
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "model_home_wp": model_home_wp,
            "home_spread": home_spread,
            "home_spread_odds": home_spread_odds,
            "away_spread_odds": away_spread_odds,
            "model_home_cover": model_home_cover,
            "model_away_cover": model_away_cover,
            "market_home_cover_nv": home_cover_nv,
            "market_away_cover_nv": away_cover_nv,
            "home_edge": home_edge_info["edge"],
            "away_edge": away_edge_info["edge"],
            "margin": margin,
            "ml_bet_side": row.get("bet_side"),
            "ml_bet_profit": row.get("bet_profit", 0),
            "spread_bet_side": None,
            "spread_bet_odds": None,
            "spread_bet_edge": None,
            "spread_bet_stake": 0,
            "spread_bet_profit": 0,
            "spread_bet_won": None,
        }

        # Home spread bet
        if (ML_MIN_EDGE <= home_edge_info["edge"] <= ML_MAX_EDGE
                and home_edge_info["ev_per_unit"] > 0
                and confidence >= ML_MIN_CONFIDENCE):
            sizing = size_bet(home_edge_info["adjusted_prob"], american_to_decimal(home_spread_odds),
                              bankroll_val, KELLY_FRACTION, MAX_BET_FRACTION)
            if sizing["bet_dollars"] > 0:
                won = bool(home_covered)
                decimal_odds = american_to_decimal(home_spread_odds)
                profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

                bet_info["spread_bet_side"] = f"home {home_spread}"
                bet_info["spread_bet_odds"] = home_spread_odds
                bet_info["spread_bet_edge"] = home_edge_info["edge"]
                bet_info["spread_bet_stake"] = sizing["bet_dollars"]
                bet_info["spread_bet_profit"] = round(profit, 2)
                bet_info["spread_bet_won"] = won

        # Away spread bet (only if no home bet)
        if bet_info["spread_bet_side"] is None:
            if (ML_MIN_EDGE <= away_edge_info["edge"] <= ML_MAX_EDGE
                    and away_edge_info["ev_per_unit"] > 0
                    and confidence >= ML_MIN_CONFIDENCE):
                sizing = size_bet(away_edge_info["adjusted_prob"], american_to_decimal(away_spread_odds),
                                  bankroll_val, KELLY_FRACTION, MAX_BET_FRACTION)
                if sizing["bet_dollars"] > 0:
                    won = bool(away_covered)
                    decimal_odds = american_to_decimal(away_spread_odds)
                    profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

                    bet_info["spread_bet_side"] = f"away {away_spread}"
                    bet_info["spread_bet_odds"] = away_spread_odds
                    bet_info["spread_bet_edge"] = away_edge_info["edge"]
                    bet_info["spread_bet_stake"] = sizing["bet_dollars"]
                    bet_info["spread_bet_profit"] = round(profit, 2)
                    bet_info["spread_bet_won"] = won

        spread_bets.append(bet_info)

    print(f"  Matched {matched}/{len(bt)} games to spread lines")

    # Analyze results
    df = pd.DataFrame(spread_bets)
    placed = df[df["spread_bet_side"].notna()]

    print(f"\n{'=' * 60}")
    print("  RUN LINE (SPREAD) RESULTS — 2025")
    print(f"{'=' * 60}")
    print(f"  Total games with spread lines: {matched}")
    print(f"  Spread bets placed: {len(placed)}")

    if len(placed) > 0:
        wins = placed["spread_bet_won"].sum()
        total_profit = placed["spread_bet_profit"].sum()
        total_staked = placed["spread_bet_stake"].sum()
        roi = (total_profit / total_staked * 100) if total_staked > 0 else 0
        win_rate = wins / len(placed) * 100

        print(f"  Win rate: {wins}/{len(placed)} ({win_rate:.1f}%)")
        print(f"  Total staked: ${total_staked:,.2f}")
        print(f"  Total profit: ${total_profit:,.2f}")
        print(f"  ROI: {roi:+.1f}%")

        # Breakdown by side
        fav = placed[placed["spread_bet_side"].str.contains("-1.5")]
        dog = placed[placed["spread_bet_side"].str.contains("\\+1.5") | placed["spread_bet_side"].str.contains("1.5")]
        dog = placed[~placed.index.isin(fav.index)]

        if len(fav) > 0:
            fav_profit = fav["spread_bet_profit"].sum()
            fav_staked = fav["spread_bet_stake"].sum()
            fav_roi = (fav_profit / fav_staked * 100) if fav_staked > 0 else 0
            print(f"\n  Favorite (-1.5): {len(fav)} bets, ${fav_profit:,.2f} profit ({fav_roi:+.1f}% ROI)")

        if len(dog) > 0:
            dog_profit = dog["spread_bet_profit"].sum()
            dog_staked = dog["spread_bet_stake"].sum()
            dog_roi = (dog_profit / dog_staked * 100) if dog_staked > 0 else 0
            print(f"  Underdog (+1.5):  {len(dog)} bets, ${dog_profit:,.2f} profit ({dog_roi:+.1f}% ROI)")

        # Average edge
        print(f"\n  Average edge: {placed['spread_bet_edge'].mean():.1%}")
        print(f"  Median edge:  {placed['spread_bet_edge'].median():.1%}")

        # Compare with ML bets on same games
        ml_placed = df[df["ml_bet_side"].notna()]
        overlap = placed[placed["ml_bet_side"].notna()]
        print(f"\n  Games with BOTH ML + spread bets: {len(overlap)}")

        # Combined P&L (ML + spreads)
        ml_profit_2025 = df["ml_bet_profit"].sum()
        print(f"\n  ML profit (all games):      ${ml_profit_2025:,.2f}")
        print(f"  Spread profit:               ${total_profit:,.2f}")
        print(f"  Combined:                    ${ml_profit_2025 + total_profit:,.2f}")

    # Save detailed results
    out_path = DATA_DIR / "spread_analysis_2025.csv"
    df.to_csv(out_path, index=False)
    print(f"\n  Saved detailed results to {out_path}")


if __name__ == "__main__":
    main()
