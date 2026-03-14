"""
Daily pipeline: fetch today's odds + lineups, run simulations, find edges, output picks.

Usage:
    python scripts/run_daily.py --bankroll 1000
    python scripts/run_daily.py --date 2026-04-01      # specific date
    python scripts/run_daily.py --sims 5000             # fewer sims for speed
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from config import (
    N_SIMULATIONS,
    ML_MIN_EDGE, ML_MAX_EDGE, ML_MIN_CONFIDENCE, ML_ALPHA,
    TOTALS_MIN_EDGE, TOTALS_MAX_EDGE, TOTALS_MIN_CONFIDENCE, TOTALS_ALPHA,
    KELLY_FRACTION, MAX_BET_FRACTION, HOME_FIELD_ADVANTAGE, ELO_BLEND_WEIGHT,
    DATA_DIR,
)
from src.data.fetch import fetch_daily_lineups, team_abbrev, TEAM_NAME_TO_ABBREV
from src.data.state import load_state, save_state
from src.betting.odds import (
    fetch_mlb_odds, parse_odds_response,
    american_to_prob, american_to_decimal, remove_vig, prob_to_american,
)
from src.betting.edge import calculate_edge, compute_game_confidence
from src.betting.kelly import size_bet
from src.features.batting import build_batter_profile
from src.features.pitching import build_pitcher_profile, build_bullpen_profile
from src.features.park_factors import get_park_factors
from src.simulation.constants import LEAGUE_RATES
from src.simulation.game_sim import monte_carlo_win_probability

DAILY_DIR = DATA_DIR / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)

_DEFAULT_PITCHER = {
    "throws": "R",
    "L": LEAGUE_RATES.copy(),
    "R": LEAGUE_RATES.copy(),
}

_DEFAULT_BATTER = {
    "profile": {"R": LEAGUE_RATES.copy(), "L": LEAGUE_RATES.copy()},
    "bats": "R",
    "speed": 100,
}


def run_daily(
    bankroll: float = 1000.0,
    date: str = None,
    n_sims: int = N_SIMULATIONS,
    include_spring: bool = False,
):
    today = date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'=' * 60}")
    print(f"  MLB Betting Model — {today}")
    print(f"  Bankroll: ${bankroll:,.2f}")
    print(f"{'=' * 60}")

    # --- 1. Load persisted state ---
    print("\nLoading state...")
    state = load_state()
    if state is None:
        print("  ERROR: No state found. Run: python scripts/init_season.py")
        return
    cumulative = state["cumulative"]
    elo = state["elo"]
    batter_speeds = state["batter_speeds"]
    bankroll = bankroll or state["bankroll"]
    print(f"  {cumulative.num_batters} batters, {cumulative.num_pitchers} pitchers tracked")

    # --- 2. Fetch today's schedule + lineups ---
    print(f"\nFetching lineups for {today}...")
    try:
        games = fetch_daily_lineups(today, include_spring=include_spring)
    except Exception as e:
        print(f"  Error fetching lineups: {e}")
        return

    if not games:
        print("  No games today.")
        return
    print(f"  {len(games)} games found")

    # --- 3. Fetch live odds ---
    sport_key = "baseball_mlb_preseason" if include_spring else None
    print("\nFetching moneyline odds...")
    try:
        raw_h2h = fetch_mlb_odds(markets="h2h", sport_key=sport_key)
        odds_h2h = parse_odds_response(raw_h2h)
    except Exception as e:
        print(f"  Error fetching odds: {e}")
        odds_h2h = pd.DataFrame()

    print("Fetching totals odds...")
    try:
        raw_totals = fetch_mlb_odds(markets="totals", sport_key=sport_key)
        odds_totals = _parse_totals_response(raw_totals)
    except Exception:
        odds_totals = pd.DataFrame()

    if odds_h2h.empty:
        print("  No odds available. Simulating without betting analysis.")

    # --- 4. Build profiles from cumulative state ---
    print("\nBuilding player profiles...")
    batter_rates = cumulative.to_batter_rates_df()
    pitcher_rates = cumulative.to_pitcher_rates_df()
    batter_profiles = _build_profiles(batter_rates, "batter")
    pitcher_profiles = _build_profiles(pitcher_rates, "pitcher")
    batter_hands = cumulative.get_batter_handedness()

    team_reliever_rates = cumulative.get_team_reliever_rates()
    team_bullpen_profiles = {
        team: build_bullpen_profile(df) for team, df in team_reliever_rates.items()
    }
    print(f"  {len(batter_profiles)} batters, {len(pitcher_profiles)} pitchers, "
          f"{len(team_bullpen_profiles)} bullpens")

    # --- 5. Simulate each game ---
    print(f"\nSimulating {len(games)} games ({n_sims} sims each)...")
    output_games = []
    picks = []
    total_wagered = 0.0

    for g in games:
        home_abbr = team_abbrev(g["home_team"])
        away_abbr = team_abbrev(g["away_team"])
        home_lineup = g.get("home_lineup", [])
        away_lineup = g.get("away_lineup", [])

        # Skip games without lineups
        if len(home_lineup) < 9 or len(away_lineup) < 9:
            print(f"  {away_abbr} @ {home_abbr}: skipping (lineups not confirmed)")
            continue

        # Resolve starter IDs from the schedule data
        home_starter_id = _resolve_starter_id(g, "home", pitcher_profiles)
        away_starter_id = _resolve_starter_id(g, "away", pitcher_profiles)

        # Build simulation inputs
        sim_home_lineup = _build_lineup(home_lineup, batter_profiles, batter_hands, batter_speeds)
        sim_away_lineup = _build_lineup(away_lineup, batter_profiles, batter_hands, batter_speeds)
        home_starter = _get_pitcher(home_starter_id, pitcher_profiles)
        away_starter = _get_pitcher(away_starter_id, pitcher_profiles)
        home_bullpen = team_bullpen_profiles.get(home_abbr, _DEFAULT_PITCHER.copy())
        away_bullpen = team_bullpen_profiles.get(away_abbr, _DEFAULT_PITCHER.copy())
        park = get_park_factors(home_abbr)

        # Run simulation
        result = monte_carlo_win_probability(
            home_lineup=sim_home_lineup,
            away_lineup=sim_away_lineup,
            home_starter=home_starter,
            away_starter=away_starter,
            home_bullpen=home_bullpen,
            away_bullpen=away_bullpen,
            park_factors=park,
            n_simulations=n_sims,
        )

        # Apply HFA + Elo blend
        raw_home = result["home_win_prob"]
        sim_home = min(raw_home + HOME_FIELD_ADVANTAGE, 0.99)
        elo_prob = elo.expected_win_prob(home_abbr, away_abbr)
        if ELO_BLEND_WEIGHT > 0:
            model_home = (1 - ELO_BLEND_WEIGHT) * sim_home + ELO_BLEND_WEIGHT * elo_prob
            model_home = max(0.01, min(0.99, model_home))
        else:
            model_home = sim_home
        model_away = 1.0 - model_home

        # Build game output
        game_out = {
            "away": away_abbr,
            "home": home_abbr,
            "away_pitcher": g.get("away_starter", "Unknown"),
            "home_pitcher": g.get("home_starter", "Unknown"),
            "model_home_wp": round(model_home, 4),
            "model_away_wp": round(model_away, 4),
            "avg_total_runs": round(result["avg_total_runs"], 2),
            "elo_home_wp": round(elo_prob, 4),
            "sim_home_wp": round(sim_home, 4),
        }

        # --- Match to odds and find edges ---
        odds_row = _match_live_odds(odds_h2h, g["home_team"], g["away_team"])
        if odds_row is not None:
            market_home_nv = odds_row["home_no_vig_prob"]
            market_away_nv = odds_row["away_no_vig_prob"]
            best_home_odds = odds_row["best_home_odds"]
            best_away_odds = odds_row["best_away_odds"]

            game_out["market_home_wp"] = round(market_home_nv, 4)
            game_out["market_away_wp"] = round(market_away_nv, 4)

            # Per-sportsbook odds for display
            books_home = odds_row.get("books_home", {})
            books_away = odds_row.get("books_away", {})
            if isinstance(books_home, dict):
                game_out["books_home"] = {k: int(v) for k, v in books_home.items()}
            if isinstance(books_away, dict):
                game_out["books_away"] = {k: int(v) for k, v in books_away.items()}

            confidence = compute_game_confidence(
                cumulative_pitchers=cumulative.num_pitchers,
                model_prob=model_home,
                market_prob=market_home_nv,
            )
            game_out["confidence"] = round(confidence, 3)

            # Check ML edges (home and away)
            pick = _evaluate_ml_edge(
                model_home, model_away,
                market_home_nv, market_away_nv,
                best_home_odds, best_away_odds,
                confidence, bankroll,
                home_abbr, away_abbr,
                g.get("home_starter", ""), g.get("away_starter", ""),
                elo_prob, park, model_home, sim_home,
            )
            if pick:
                # Attach per-sportsbook odds for the picked side
                pick_side_books = books_home if pick.get("side") == "home" else books_away
                if isinstance(pick_side_books, dict):
                    pick["sportsbook_odds"] = {
                        k: int(v) for k, v in pick_side_books.items()
                    }
                game_out["pick"] = pick["pick"]
                game_out["edge_pct"] = pick["edge_pct"]
                game_out["odds"] = pick["odds"]
                game_out["kelly_fraction"] = pick["kelly_fraction"]
                game_out["explanation"] = pick["explanation"]
                picks.append(pick)
                total_wagered += pick.get("wager", 0)

        # --- Totals ---
        totals_row = _match_live_totals(odds_totals, g["home_team"], g["away_team"])
        if totals_row is not None:
            total_runs_dist = result["total_runs_dist"]
            totals_pick = _evaluate_totals_edge(
                total_runs_dist, totals_row, confidence if odds_row is not None else 0.8,
                bankroll, home_abbr, away_abbr,
            )
            if totals_pick:
                game_out["totals_pick"] = totals_pick["pick"]
                game_out["totals_edge_pct"] = totals_pick["edge_pct"]
                picks.append(totals_pick)
                total_wagered += totals_pick.get("wager", 0)

        output_games.append(game_out)
        _print_game_summary(game_out)

    # --- 6. Write output JSON ---
    output = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "games": output_games,
        "picks": picks,
        "bankroll": {
            "current": round(bankroll, 2),
            "today_wagered": round(total_wagered, 2),
        },
    }

    output_path = DAILY_DIR / f"{today}.json"
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Picks saved to {output_path}")

    # --- 7. Print summary ---
    print(f"\n{'=' * 60}")
    print(f"  SUMMARY — {today}")
    print(f"{'=' * 60}")
    print(f"  Games analyzed: {len(output_games)}")
    print(f"  Picks: {len(picks)}")
    print(f"  Total wagered: ${total_wagered:,.2f}")
    print(f"  Bankroll: ${bankroll:,.2f}")

    if picks:
        print(f"\n  TODAY'S PICKS:")
        for p in picks:
            print(f"    {p['pick']:<20s}  edge={p['edge_pct']:.1f}%  odds={p['odds']}  "
                  f"wager=${p.get('wager', 0):,.2f}")

    return output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_profiles(rates_df: pd.DataFrame, kind: str) -> dict:
    """Build profiles from cumulative rates DataFrame."""
    profiles = {}
    if rates_df.empty:
        return profiles
    id_col = "batter_id" if kind == "batter" else "pitcher_id"
    build_fn = build_batter_profile if kind == "batter" else build_pitcher_profile
    for _, row in rates_df.iterrows():
        pid = int(row[id_col])
        try:
            profiles[pid] = build_fn(row)
        except Exception:
            continue
    return profiles


def _build_lineup(lineup_ids, batter_profiles, batter_hands, batter_speeds):
    """Build a 9-batter lineup list for the sim engine."""
    if batter_speeds is None:
        batter_speeds = {}
    lineup = []
    for pid in lineup_ids[:9]:
        pid = int(pid)
        prof = batter_profiles.get(pid)
        if prof is not None:
            hand = batter_hands.get(pid, "R")
            speed = batter_speeds.get(pid, 100)
            lineup.append({"profile": prof, "bats": hand, "speed": speed})
        else:
            lineup.append(_DEFAULT_BATTER)
    while len(lineup) < 9:
        lineup.append(_DEFAULT_BATTER)
    return lineup


def _get_pitcher(pitcher_id, pitcher_profiles):
    """Look up pitcher profile, fallback to league avg."""
    if pitcher_id is None:
        return _DEFAULT_PITCHER.copy()
    prof = pitcher_profiles.get(int(pitcher_id))
    return prof if prof is not None else _DEFAULT_PITCHER.copy()


def _resolve_starter_id(game_info, side, pitcher_profiles):
    """
    Try to resolve a starter's MLBAM ID from the schedule data.
    The statsapi schedule includes probable pitcher names but not always IDs.
    We look for pitcher_id fields or fall back to name matching.
    """
    # Try direct ID fields (sometimes available in boxscore data)
    id_key = f"{side}_probable_pitcher_id"
    if id_key in game_info and game_info[id_key]:
        return int(game_info[id_key])

    # statsapi.schedule doesn't reliably return pitcher IDs for future games.
    # For now, return None (league-avg starter) when we can't resolve.
    return None


def _match_live_odds(odds_df, home_team_full, away_team_full):
    """Match a game to live odds by team name."""
    if odds_df is None or odds_df.empty:
        return None
    # The Odds API uses full team names
    mask = (
        (odds_df["home_team"] == home_team_full)
        & (odds_df["away_team"] == away_team_full)
    )
    matches = odds_df[mask]
    if not matches.empty:
        return matches.iloc[0]
    # Fuzzy: try abbreviation matching
    home_abbr = TEAM_NAME_TO_ABBREV.get(home_team_full, "")
    away_abbr = TEAM_NAME_TO_ABBREV.get(away_team_full, "")
    for _, row in odds_df.iterrows():
        if (TEAM_NAME_TO_ABBREV.get(row["home_team"]) == home_abbr
                and TEAM_NAME_TO_ABBREV.get(row["away_team"]) == away_abbr):
            return row
    return None


def _match_live_totals(totals_df, home_team_full, away_team_full):
    """Match a game to live totals odds."""
    if totals_df is None or totals_df.empty:
        return None
    mask = (
        (totals_df["home_team"] == home_team_full)
        & (totals_df["away_team"] == away_team_full)
    )
    matches = totals_df[mask]
    return matches.iloc[0] if not matches.empty else None


def _parse_totals_response(odds_data):
    """Parse The Odds API totals response into a DataFrame."""
    rows = []
    for game in odds_data:
        home = game["home_team"]
        away = game["away_team"]

        best_over_odds, best_under_odds = None, None
        best_over_book, best_under_book = None, None
        total_line = None

        for bm in game.get("bookmakers", []):
            key = bm["key"]
            for mkt in bm.get("markets", []):
                if mkt["key"] != "totals":
                    continue
                for oc in mkt["outcomes"]:
                    price = oc["price"]
                    point = oc.get("point")
                    if point is not None:
                        total_line = point
                    if oc["name"] == "Over":
                        if best_over_odds is None or price > best_over_odds:
                            best_over_odds, best_over_book = price, key
                    elif oc["name"] == "Under":
                        if best_under_odds is None or price > best_under_odds:
                            best_under_odds, best_under_book = price, key

        if best_over_odds is None or best_under_odds is None or total_line is None:
            continue

        over_imp = american_to_prob(best_over_odds)
        under_imp = american_to_prob(best_under_odds)
        over_nv, under_nv = remove_vig(over_imp, under_imp)

        rows.append({
            "home_team": home,
            "away_team": away,
            "total_line": total_line,
            "best_over_odds": best_over_odds,
            "best_under_odds": best_under_odds,
            "over_no_vig_prob": over_nv,
            "under_no_vig_prob": under_nv,
        })
    return pd.DataFrame(rows)


def _evaluate_ml_edge(
    model_home, model_away,
    market_home_nv, market_away_nv,
    best_home_odds, best_away_odds,
    confidence, bankroll,
    home_abbr, away_abbr,
    home_pitcher, away_pitcher,
    elo_prob, park, adj_home, sim_home,
):
    """Check for ML edges and return a pick dict if found."""
    home_edge_info = calculate_edge(model_home, market_home_nv, best_home_odds, confidence, alpha=ML_ALPHA)
    away_edge_info = calculate_edge(model_away, market_away_nv, best_away_odds, confidence, alpha=ML_ALPHA)

    # Check home
    if (ML_MIN_EDGE <= home_edge_info["edge"] <= ML_MAX_EDGE
            and home_edge_info["ev_per_unit"] > 0
            and confidence >= ML_MIN_CONFIDENCE):
        sizing = size_bet(home_edge_info["adjusted_prob"], american_to_decimal(best_home_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            odds_str = f"+{int(best_home_odds)}" if best_home_odds > 0 else str(int(best_home_odds))
            explanation = _generate_explanation(
                home_abbr, away_abbr, "home", home_pitcher, away_pitcher,
                home_edge_info, elo_prob, park, adj_home,
            )
            return {
                "pick": f"{home_abbr} ML",
                "side": "home",
                "team": home_abbr,
                "opponent": away_abbr,
                "edge_pct": round(home_edge_info["edge"] * 100, 1),
                "confidence": round(confidence, 3),
                "odds": odds_str,
                "kelly_fraction": round(sizing["bet_fraction"], 4),
                "wager": sizing["bet_dollars"],
                "explanation": explanation,
                "type": "moneyline",
            }

    # Check away
    if (ML_MIN_EDGE <= away_edge_info["edge"] <= ML_MAX_EDGE
            and away_edge_info["ev_per_unit"] > 0
            and confidence >= ML_MIN_CONFIDENCE):
        sizing = size_bet(away_edge_info["adjusted_prob"], american_to_decimal(best_away_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            odds_str = f"+{int(best_away_odds)}" if best_away_odds > 0 else str(int(best_away_odds))
            explanation = _generate_explanation(
                away_abbr, home_abbr, "away", away_pitcher, home_pitcher,
                away_edge_info, 1.0 - elo_prob, park, 1.0 - adj_home,
            )
            return {
                "pick": f"{away_abbr} ML",
                "side": "away",
                "team": away_abbr,
                "opponent": home_abbr,
                "edge_pct": round(away_edge_info["edge"] * 100, 1),
                "confidence": round(confidence, 3),
                "odds": odds_str,
                "kelly_fraction": round(sizing["bet_fraction"], 4),
                "wager": sizing["bet_dollars"],
                "explanation": explanation,
                "type": "moneyline",
            }

    return None


def _evaluate_totals_edge(total_runs_dist, totals_row, confidence, bankroll, home_abbr, away_abbr):
    """Check for totals edges."""
    line = totals_row["total_line"]
    over_nv = totals_row["over_no_vig_prob"]
    under_nv = totals_row["under_no_vig_prob"]
    best_over_odds = totals_row["best_over_odds"]
    best_under_odds = totals_row["best_under_odds"]

    over_count = np.sum(total_runs_dist > line)
    under_count = np.sum(total_runs_dist < line)
    decided = over_count + under_count
    if decided == 0:
        return None

    model_over = float(over_count / decided)
    model_under = float(under_count / decided)

    over_edge = calculate_edge(model_over, over_nv, best_over_odds, confidence, alpha=TOTALS_ALPHA)
    under_edge = calculate_edge(model_under, under_nv, best_under_odds, confidence, alpha=TOTALS_ALPHA)

    # Check over
    if (TOTALS_MIN_EDGE <= over_edge["edge"] <= TOTALS_MAX_EDGE
            and over_edge["ev_per_unit"] > 0
            and confidence >= TOTALS_MIN_CONFIDENCE):
        sizing = size_bet(over_edge["adjusted_prob"], american_to_decimal(best_over_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            odds_str = f"+{int(best_over_odds)}" if best_over_odds > 0 else str(int(best_over_odds))
            return {
                "pick": f"{away_abbr}@{home_abbr} OVER {line}",
                "edge_pct": round(over_edge["edge"] * 100, 1),
                "odds": odds_str,
                "kelly_fraction": round(sizing["bet_fraction"], 4),
                "wager": sizing["bet_dollars"],
                "explanation": f"Model projects {model_over:.0%} over vs {over_nv:.0%} market.",
                "type": "totals",
            }

    # Check under
    if (TOTALS_MIN_EDGE <= under_edge["edge"] <= TOTALS_MAX_EDGE
            and under_edge["ev_per_unit"] > 0
            and confidence >= TOTALS_MIN_CONFIDENCE):
        sizing = size_bet(under_edge["adjusted_prob"], american_to_decimal(best_under_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            odds_str = f"+{int(best_under_odds)}" if best_under_odds > 0 else str(int(best_under_odds))
            return {
                "pick": f"{away_abbr}@{home_abbr} UNDER {line}",
                "edge_pct": round(under_edge["edge"] * 100, 1),
                "odds": odds_str,
                "kelly_fraction": round(sizing["bet_fraction"], 4),
                "wager": sizing["bet_dollars"],
                "explanation": f"Model projects {model_under:.0%} under vs {under_nv:.0%} market.",
                "type": "totals",
            }

    return None


def _generate_explanation(team, opponent, side, team_pitcher, opp_pitcher,
                          edge_info, elo_prob, park, model_prob):
    """Generate a 1-2 sentence explanation for a pick."""
    parts = []

    # Pitcher matchup
    if team_pitcher and opp_pitcher:
        parts.append(f"{team_pitcher} vs {opp_pitcher}")

    # Elo/team strength
    if elo_prob > 0.55:
        parts.append(f"Elo favors {team} ({elo_prob:.0%})")
    elif elo_prob < 0.45:
        parts.append(f"Elo edge despite market undervaluing {team}")

    # Edge magnitude
    edge_pct = edge_info["edge"] * 100
    parts.append(f"{edge_pct:.1f}% edge at {prob_to_american(edge_info['market_prob']):+.0f} market")

    # Park factor note for extreme parks
    hr_factor = park.get("HR", 1.0)
    if hr_factor >= 1.10:
        parts.append("hitter-friendly park")
    elif hr_factor <= 0.90:
        parts.append("pitcher-friendly park")

    return ". ".join(parts[:3]) + "."


def _print_game_summary(game_out):
    """Print a one-line summary for a game."""
    away = game_out["away"]
    home = game_out["home"]
    model_wp = game_out["model_home_wp"]
    market_wp = game_out.get("market_home_wp", "N/A")
    pick = game_out.get("pick", "-")
    edge = game_out.get("edge_pct", 0)

    market_str = f"{market_wp:.1%}" if isinstance(market_wp, float) else market_wp
    print(f"  {away:>3s} @ {home:<3s}  model={model_wp:.1%}  market={market_str}  "
          f"pick={pick}  edge={edge:.1f}%")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Daily betting pipeline")
    parser.add_argument("--bankroll", type=float, default=None,
                        help="Override bankroll (default: use saved state)")
    parser.add_argument("--date", type=str, default=None,
                        help="Date to analyze (YYYY-MM-DD, default: today)")
    parser.add_argument("--sims", type=int, default=N_SIMULATIONS,
                        help=f"Number of simulations per game (default: {N_SIMULATIONS})")
    parser.add_argument("--spring", action="store_true",
                        help="Include spring training games")
    args = parser.parse_args()

    bankroll = args.bankroll
    if bankroll is None:
        state = load_state()
        bankroll = state["bankroll"] if state else 1000.0

    run_daily(bankroll=bankroll, date=args.date, n_sims=args.sims,
              include_spring=args.spring)
