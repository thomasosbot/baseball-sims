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
import statsapi

from config import (
    N_SIMULATIONS,
    ML_MIN_EDGE, ML_MAX_EDGE, ML_MIN_CONFIDENCE, ML_ALPHA,
    TOTALS_MIN_EDGE, TOTALS_MAX_EDGE, TOTALS_MIN_CONFIDENCE, TOTALS_ALPHA,
    KELLY_FRACTION, MAX_BET_FRACTION, HOME_FIELD_ADVANTAGE, ELO_BLEND_WEIGHT,
    DATA_DIR,
)
from src.data.fetch import (
    fetch_daily_lineups, fetch_rotogrinders_lineups, resolve_rg_player_to_id,
    team_abbrev, TEAM_NAME_TO_ABBREV,
)
from src.data.state import load_state, save_state
from src.betting.odds import (
    fetch_mlb_odds, parse_odds_response,
    american_to_prob, american_to_decimal, remove_vig, prob_to_american,
)
from src.betting.edge import calculate_edge, compute_game_confidence
from src.betting.kelly import size_bet
from src.features.batting import build_batter_profile
from src.features.pitching import build_pitcher_profile, build_bullpen_profile, build_tiered_bullpen_profiles
from src.features.park_factors import get_park_factors
from src.features.weather import (
    compute_weather_factors, merge_weather_into_park_factors,
    compass_to_field_relative, PARK_CF_BEARING,
    RETRACTABLE_ROOF_PARKS, FIXED_DOME_PARKS,
)
from src.simulation.constants import LEAGUE_RATES
from src.simulation.game_sim import monte_carlo_win_probability

DAILY_DIR = DATA_DIR / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)

# Cache for player name lookups via statsapi
_player_name_cache = {}


def _resolve_player_name(pid, cumulative=None):
    """Resolve a player MLBAM ID to a display name."""
    pid = int(pid)
    # 1. Check cumulative state (fastest, from Statcast data)
    if cumulative:
        name = cumulative._batter_names.get(pid) or cumulative._pitcher_names.get(pid)
        if name and not name.replace(" ", "").isdigit():
            return name
    # 2. Check local cache
    if pid in _player_name_cache:
        return _player_name_cache[pid]
    # 3. Look up via statsapi
    try:
        results = statsapi.lookup_player(pid)
        if results:
            name = results[0].get("fullName", str(pid))
            _player_name_cache[pid] = name
            return name
    except Exception:
        pass
    return str(pid)


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


def _fetch_preview_lineups(date: str, cumulative, include_spring: bool) -> list:
    """
    Fetch projected lineups from RotoGrinders for night-before preview run.
    Resolves player names to MLBAM IDs via cumulative state + MLB API.
    Games not on RotoGrinders get platoon-projected lineups as fallback.
    """
    rg_games = fetch_rotogrinders_lineups(date)

    # Also fetch full schedule from statsapi for all games
    m, d, y = date[5:7], date[8:10], date[:4]
    try:
        schedule = statsapi.schedule(date=f"{m}/{d}/{y}")
    except Exception:
        schedule = []

    # Build lookup: (home_abbr, away_abbr) → schedule entry
    sched_lookup = {}
    allowed_types = {"R", "S"} if include_spring else {"R"}
    for g in schedule:
        if g.get("game_type") not in allowed_types:
            continue
        ha = team_abbrev(g["home_name"])
        aa = team_abbrev(g["away_name"])
        sched_lookup[(ha, aa)] = g

    # Build RG lookup: (home_abbr, away_abbr) → rg game
    rg_lookup = {}
    for rg in (rg_games or []):
        rg_lookup[(rg["home_team"], rg["away_team"])] = rg

    rg_count = 0
    fallback_count = 0
    results = []

    for (home_abbr, away_abbr), sched in sched_lookup.items():
        rg = rg_lookup.get((home_abbr, away_abbr))

        if rg and len(rg["home_lineup"]) >= 1:
            # Use RotoGrinders lineup
            home_lineup_ids = []
            for name in rg["home_lineup"][:9]:
                pid = resolve_rg_player_to_id(name, cumulative)
                if pid:
                    home_lineup_ids.append(pid)

            away_lineup_ids = []
            for name in rg["away_lineup"][:9]:
                pid = resolve_rg_player_to_id(name, cumulative)
                if pid:
                    away_lineup_ids.append(pid)

            if rg["home_confirmed"] and rg["away_confirmed"]:
                lineup_status = "confirmed"
            else:
                lineup_status = "projected"

            resolved_pct = (len(home_lineup_ids) + len(away_lineup_ids)) / 18 * 100
            print(f"  {away_abbr} @ {home_abbr}: RotoGrinders {len(away_lineup_ids)}+{len(home_lineup_ids)} IDs ({resolved_pct:.0f}%), "
                  f"{'confirmed' if lineup_status == 'confirmed' else 'projected'}")
            rg_count += 1

            results.append({
                "game_id": sched.get("game_id", 0),
                "game_date": date,
                "home_team": sched.get("home_name", home_abbr),
                "away_team": sched.get("away_name", away_abbr),
                "home_id": sched.get("home_id", 0),
                "away_id": sched.get("away_id", 0),
                "venue": sched.get("venue_name", ""),
                "home_lineup": home_lineup_ids,
                "away_lineup": away_lineup_ids,
                "home_starter": rg["home_pitcher"],
                "away_starter": rg["away_pitcher"],
                "home_score": None,
                "away_score": None,
                "status": "Preview",
                "lineup_status": lineup_status,
            })
        else:
            # Fallback: use platoon-projected lineups
            fallback_count += 1
            # This game will be picked up by fetch_daily_lineups with platoon projection
            pass

    # For games not covered by RG, fall back to platoon projections
    if fallback_count > 0 or not rg_games:
        print(f"  {fallback_count} games not on RotoGrinders, using platoon projections...")
        platoon_games = fetch_daily_lineups(
            date, include_spring=include_spring,
            use_projected=True, cumulative=cumulative,
        )
        # Merge: only add games not already covered by RG
        rg_keys = {(r["home_team"], r["away_team"]) for r in rg_games or []}
        for pg in platoon_games:
            ha = team_abbrev(pg["home_team"])
            aa = team_abbrev(pg["away_team"])
            if (ha, aa) not in rg_keys:
                pg["lineup_status"] = "projected"
                results.append(pg)

    total_rg = rg_count
    total_platoon = len(results) - rg_count
    print(f"  Total: {len(results)} games ({total_rg} RotoGrinders, {total_platoon} platoon projected)")

    return results


def run_daily(
    bankroll: float = 1000.0,
    date: str = None,
    n_sims: int = N_SIMULATIONS,
    include_spring: bool = False,
    mode: str = "late",
):
    # Spring training: loosen totals thresholds for pressure testing
    if include_spring:
        global TOTALS_ALPHA, TOTALS_MIN_EDGE, TOTALS_MIN_CONFIDENCE
        TOTALS_ALPHA = 0.7       # let more model signal through (was 0.3)
        TOTALS_MIN_EDGE = 0.03   # lower edge floor to 3% (was 7%)
        TOTALS_MIN_CONFIDENCE = 0.0

    today = date or datetime.now().strftime("%Y-%m-%d")
    print(f"\n{'=' * 60}")
    print(f"  MLB Betting Model — {today}")
    if include_spring:
        print(f"  SPRING TRAINING — totals pressure test (α={TOTALS_ALPHA}, min edge={TOTALS_MIN_EDGE*100:.0f}%)")
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
    use_projected = (mode in ("early", "preview"))
    print(f"\nFetching lineups for {today} (mode={mode}, projected={use_projected})...")

    if mode == "preview":
        # Night-before run: use RotoGrinders projected lineups
        games = _fetch_preview_lineups(today, cumulative, include_spring)
    else:
        try:
            games = fetch_daily_lineups(today, include_spring=include_spring, use_projected=use_projected, cumulative=cumulative)
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

    # Totals odds fetch disabled for 2026 (totals betting consistently negative)
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
        team: build_tiered_bullpen_profiles(df) for team, df in team_reliever_rates.items()
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

        lineup_status = g.get("lineup_status", "confirmed")

        # Games without lineups: include in output but don't simulate
        if len(home_lineup) < 9 or len(away_lineup) < 9:
            print(f"  {away_abbr} @ {home_abbr}: lineups pending")
            output_games.append({
                "away": away_abbr,
                "home": home_abbr,
                "away_pitcher": g.get("away_starter", "TBD"),
                "home_pitcher": g.get("home_starter", "TBD"),
                "status": "lineups_pending",
                "lineup_status": "pending",
            })
            continue

        if lineup_status == "projected":
            print(f"  {away_abbr} @ {home_abbr}: using projected lineups")

        # Resolve starter IDs from the schedule data
        home_starter_id = _resolve_starter_id(g, "home", pitcher_profiles)
        away_starter_id = _resolve_starter_id(g, "away", pitcher_profiles)

        # Build simulation inputs
        sim_home_lineup = _build_lineup(home_lineup, batter_profiles, batter_hands, batter_speeds)
        sim_away_lineup = _build_lineup(away_lineup, batter_profiles, batter_hands, batter_speeds)
        home_starter = _get_pitcher(home_starter_id, pitcher_profiles)
        away_starter = _get_pitcher(away_starter_id, pitcher_profiles)
        _default_bp = _DEFAULT_PITCHER.copy()
        home_bp = team_bullpen_profiles.get(home_abbr)
        away_bp = team_bullpen_profiles.get(away_abbr)
        if home_bp and isinstance(home_bp, tuple):
            home_bullpen_hi, home_bullpen_lo = home_bp
        else:
            home_bullpen_hi = home_bp if home_bp else _default_bp
            home_bullpen_lo = None
        if away_bp and isinstance(away_bp, tuple):
            away_bullpen_hi, away_bullpen_lo = away_bp
        else:
            away_bullpen_hi = away_bp if away_bp else _default_bp
            away_bullpen_lo = None
        park = get_park_factors(home_abbr)

        # Fetch weather forecast and adjust park factors
        weather_info = _fetch_game_weather(home_abbr, today)
        weather_factors = None
        if weather_info:
            weather_factors = compute_weather_factors(
                temperature=weather_info.get("temperature"),
                wind_speed=weather_info.get("wind_speed", 0),
                wind_direction=weather_info.get("wind_direction", ""),
                condition=weather_info.get("condition", ""),
            )
            park = merge_weather_into_park_factors(park, weather_factors)

        # Run simulation
        result = monte_carlo_win_probability(
            home_lineup=sim_home_lineup,
            away_lineup=sim_away_lineup,
            home_starter=home_starter,
            away_starter=away_starter,
            home_bullpen=home_bullpen_hi,
            away_bullpen=away_bullpen_hi,
            park_factors=park,
            n_simulations=n_sims,
            home_bullpen_lo=home_bullpen_lo,
            away_bullpen_lo=away_bullpen_lo,
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

        # Resolve lineup names for display
        home_lineup_names = [_resolve_player_name(pid, cumulative) for pid in home_lineup[:9]]
        away_lineup_names = [_resolve_player_name(pid, cumulative) for pid in away_lineup[:9]]

        # Build margin distribution histogram (home margin -10 to +10)
        margin_dist = result["margin_dist"]
        margin_labels = list(range(-10, 11))
        margin_freq = []
        n_sims_total = len(margin_dist)
        for m in margin_labels:
            margin_freq.append(round(float(np.sum(margin_dist == m)) / n_sims_total * 100, 1))

        # Build run distribution histograms (0-15 runs)
        run_labels = list(range(0, 16))
        total_runs = result["total_runs_dist"]
        home_runs = ((total_runs + margin_dist) // 2).astype(int)
        away_runs = ((total_runs - margin_dist) // 2).astype(int)
        home_run_freq = []
        away_run_freq = []
        for r in run_labels:
            home_run_freq.append(round(float(np.sum(home_runs == r)) / n_sims_total * 100, 1))
            away_run_freq.append(round(float(np.sum(away_runs == r)) / n_sims_total * 100, 1))

        # Park factors for display (round to 2 decimals)
        park_display = {k: round(v, 2) for k, v in park.items() if isinstance(v, (int, float))}

        # Weather for display
        weather_display = weather_info if weather_info else {}

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
            "lineup_status": lineup_status,
            "home_lineup_names": home_lineup_names,
            "away_lineup_names": away_lineup_names,
            "sim_detail": {
                "avg_home_runs": round(result["avg_home_runs"], 2),
                "avg_away_runs": round(result["avg_away_runs"], 2),
                "std_total_runs": round(result["std_total_runs"], 2),
                "margin_distribution": {
                    "labels": margin_labels,
                    "freq": margin_freq,
                },
                "run_distribution": {
                    "labels": run_labels,
                    "home_freq": home_run_freq,
                    "away_freq": away_run_freq,
                },
            },
            "weather": weather_display,
            "park_factors": park_display,
            "elo_home_rating": round(elo.get(home_abbr)),
            "elo_away_rating": round(elo.get(away_abbr)),
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
                game_out=game_out,
            )
            if pick:
                # Attach per-sportsbook odds for the picked side
                pick_side_books = books_home if pick.get("side") == "home" else books_away
                if isinstance(pick_side_books, dict):
                    pick["sportsbook_odds"] = {
                        k: int(v) for k, v in pick_side_books.items()
                    }
                pick["lineup_status"] = lineup_status
                game_out["pick"] = pick["pick"]
                game_out["edge_pct"] = pick["edge_pct"]
                game_out["odds"] = pick["odds"]
                game_out["kelly_fraction"] = pick["kelly_fraction"]
                game_out["explanation"] = pick["explanation"]
                picks.append(pick)
                total_wagered += pick.get("wager", 0)

        # --- Totals --- DISABLED for 2026: totals betting consistently negative
        # totals_row = _match_live_totals(odds_totals, g["home_team"], g["away_team"])
        # if totals_row is not None:
        #     total_runs_dist = result["total_runs_dist"]
        #     totals_pick = _evaluate_totals_edge(
        #         total_runs_dist, totals_row, confidence if odds_row is not None else 0.8,
        #         bankroll, home_abbr, away_abbr,
        #     )
        #     if totals_pick:
        #         game_out["totals_pick"] = totals_pick["pick"]
        #         game_out["totals_edge_pct"] = totals_pick["edge_pct"]
        #         picks.append(totals_pick)
        #         total_wagered += totals_pick.get("wager", 0)

        output_games.append(game_out)
        _print_game_summary(game_out)

    # --- 6. Write output JSON ---
    output = {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "run_mode": mode,
        "games": output_games,
        "picks": picks,
        "bankroll": {
            "current": round(bankroll, 2),
            "today_wagered": round(total_wagered, 2),
        },
    }

    output_path = DAILY_DIR / f"{today}.json"

    # Save changelog snapshot: if a previous run exists, capture the diff
    _save_changelog_snapshot(output_path, output, today, mode)

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
# Changelog
# ---------------------------------------------------------------------------

CHANGELOG_PATH = DAILY_DIR / "changelog.json"


def _save_changelog_snapshot(output_path, new_output, date, new_mode):
    """
    Compare new output against previous run for the same date.
    Appends a diff entry to changelog.json if the previous run was an
    earlier mode (preview→early, preview→late, early→late).
    """
    if not output_path.exists():
        return

    try:
        with open(output_path) as f:
            prev = json.load(f)
    except Exception:
        return

    prev_mode = prev.get("run_mode", "")
    mode_order = {"preview": 0, "early": 1, "late": 2}

    # Only log when upgrading from an earlier mode
    if mode_order.get(new_mode, 0) <= mode_order.get(prev_mode, 0):
        return

    # Build game-level diffs
    prev_games = {f"{g['away']}@{g['home']}": g for g in prev.get("games", [])}
    new_games = {f"{g['away']}@{g['home']}": g for g in new_output.get("games", [])}
    prev_picks = {p["pick"]: p for p in prev.get("picks", [])}
    new_picks = {p["pick"]: p for p in new_output.get("picks", [])}

    game_diffs = []
    for key in sorted(set(list(prev_games.keys()) + list(new_games.keys()))):
        pg = prev_games.get(key, {})
        ng = new_games.get(key, {})
        away, home = key.split("@")

        prev_home_wp = pg.get("model_home_wp", 0) or 0
        new_home_wp = ng.get("model_home_wp", 0) or 0
        wp_shift = (new_home_wp - prev_home_wp) * 100

        prev_fav = home if prev_home_wp > 0.5 else away
        prev_fav_pct = round(max(prev_home_wp, 1 - prev_home_wp) * 100, 1)
        new_fav = home if new_home_wp > 0.5 else away
        new_fav_pct = round(max(new_home_wp, 1 - new_home_wp) * 100, 1)

        game_diffs.append({
            "away": away,
            "home": home,
            "preview_fav": prev_fav if prev_home_wp else None,
            "preview_fav_pct": prev_fav_pct if prev_home_wp else None,
            "final_fav": new_fav if new_home_wp else None,
            "final_fav_pct": new_fav_pct if new_home_wp else None,
            "wp_shift": round(wp_shift, 1),
            "wp_shift_abs": round(abs(wp_shift), 1),
            "preview_edge": pg.get("edge_pct", 0) or 0,
            "final_edge": ng.get("edge_pct", 0) or 0,
        })

    # Pick changes
    pick_changes = []
    all_pick_keys = set(list(prev_picks.keys()) + list(new_picks.keys()))
    for pk in sorted(all_pick_keys):
        in_prev = pk in prev_picks
        in_new = pk in new_picks
        if in_prev and not in_new:
            pp = prev_picks[pk]
            pick_changes.append({
                "type": "dropped",
                "description": f"{pk} dropped (was {pp.get('odds', '')} at {pp.get('edge_pct', 0):.1f}% edge)",
            })
        elif in_new and not in_prev:
            np_ = new_picks[pk]
            pick_changes.append({
                "type": "added",
                "description": f"{pk} added ({np_.get('odds', '')} at {np_.get('edge_pct', 0):.1f}% edge)",
            })
        elif in_prev and in_new:
            pp = prev_picks[pk]
            np_ = new_picks[pk]
            prev_edge = pp.get("edge_pct", 0)
            new_edge = np_.get("edge_pct", 0)
            edge_diff = new_edge - prev_edge
            if abs(edge_diff) >= 0.5:
                pick_changes.append({
                    "type": "shifted",
                    "description": f"{pk} edge {prev_edge:.1f}% → {new_edge:.1f}% ({edge_diff:+.1f}%)",
                })

    entry = {
        "date": date,
        "prev_mode": prev_mode,
        "new_mode": new_mode,
        "timestamp": datetime.now().isoformat(),
        "game_diffs": game_diffs,
        "pick_changes": pick_changes,
    }

    # Load or create changelog
    changelog = []
    if CHANGELOG_PATH.exists():
        try:
            with open(CHANGELOG_PATH) as f:
                changelog = json.load(f)
        except Exception:
            changelog = []

    # Remove any existing entry for same date + transition
    changelog = [e for e in changelog if not (e["date"] == date and e["new_mode"] == new_mode)]
    changelog.append(entry)

    with open(CHANGELOG_PATH, "w") as f:
        json.dump(changelog, f, indent=2, default=str)

    print(f"  Changelog: {prev_mode}→{new_mode}, {len(pick_changes)} pick changes, "
          f"{sum(1 for g in game_diffs if g['wp_shift_abs'] >= 3)} big WP shifts")


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


# ---------------------------------------------------------------------------
# Park coordinates for Open-Meteo weather forecast
# ---------------------------------------------------------------------------

PARK_COORDS = {
    "ARI": (33.445, -112.067), "ATL": (33.891, -84.468), "BAL": (39.284, -76.622),
    "BOS": (42.346, -71.098), "CHC": (41.948, -87.656), "CWS": (41.830, -87.634),
    "CIN": (39.097, -84.507), "CLE": (41.496, -81.685), "COL": (39.756, -104.994),
    "DET": (42.339, -83.049), "HOU": (29.757, -95.355), "KC":  (39.051, -94.480),
    "LAA": (33.800, -117.883), "LAD": (34.074, -118.240), "MIA": (25.778, -80.220),
    "MIL": (43.028, -87.971), "MIN": (44.982, -93.278), "NYM": (40.757, -73.846),
    "NYY": (40.829, -73.926), "OAK": (37.751, -122.201), "PHI": (39.906, -75.167),
    "PIT": (40.447, -80.006), "SD":  (32.707, -117.157), "SF":  (37.778, -122.389),
    "SEA": (47.591, -122.332), "STL": (38.623, -90.193), "TB":  (27.768, -82.653),
    "TEX": (32.747, -97.084), "TOR": (43.641, -79.389), "WSH": (38.873, -77.007),
}


def _fetch_game_weather(home_abbr: str, game_date: str) -> dict:
    """
    Fetch weather forecast for a park from Open-Meteo (free, no API key).
    Returns dict with temperature (F), wind_speed (mph), wind_direction (field-relative).
    """
    import urllib.request
    import json as _json

    coords = PARK_COORDS.get(home_abbr)
    if coords is None:
        return {}

    # Fixed dome parks — no weather effect
    if home_abbr in FIXED_DOME_PARKS:
        return {"condition": "Dome"}

    lat, lon = coords
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,wind_speed_10m,wind_direction_10m"
        f"&temperature_unit=fahrenheit&wind_speed_unit=mph"
        f"&start_date={game_date}&end_date={game_date}"
        f"&timezone=America%2FNew_York"
    )

    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("wind_speed_10m", [])
        wind_dirs = hourly.get("wind_direction_10m", [])

        if not times:
            return {}

        # Use 7 PM local (typical first pitch) — index 19
        idx = min(19, len(times) - 1)

        temperature = temps[idx] if idx < len(temps) else None
        wind_speed = winds[idx] if idx < len(winds) else 0
        wind_bearing = wind_dirs[idx] if idx < len(wind_dirs) else 0

        # Convert compass bearing to field-relative direction
        field_dir = compass_to_field_relative(wind_bearing, home_abbr)

        # For retractable roof parks, we can't know if roof will be open
        # Conservative: assume roof closed (no weather effect) for these parks
        if home_abbr in RETRACTABLE_ROOF_PARKS:
            return {"condition": "Roof Closed"}

        return {
            "temperature": temperature,
            "wind_speed": wind_speed,
            "wind_direction": field_dir,
            "condition": "Forecast",
        }
    except Exception:
        return {}


def _evaluate_ml_edge(
    model_home, model_away,
    market_home_nv, market_away_nv,
    best_home_odds, best_away_odds,
    confidence, bankroll,
    home_abbr, away_abbr,
    home_pitcher, away_pitcher,
    elo_prob, park, adj_home, sim_home,
    game_out=None,
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
                sim_detail=game_out.get("sim_detail"),
                weather=game_out.get("weather"),
                elo_rating=game_out.get("elo_home_rating"),
                opp_elo_rating=game_out.get("elo_away_rating"),
                market_prob=market_home_nv,
                best_odds=best_home_odds,
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
                sim_detail=game_out.get("sim_detail"),
                weather=game_out.get("weather"),
                elo_rating=game_out.get("elo_away_rating"),
                opp_elo_rating=game_out.get("elo_home_rating"),
                market_prob=market_away_nv,
                best_odds=best_away_odds,
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
                          edge_info, elo_prob, park, model_prob,
                          sim_detail=None, weather=None, elo_rating=None,
                          opp_elo_rating=None, market_prob=None, best_odds=None):
    """Generate a rich, narrative explanation for a pick."""
    parts = []
    edge_pct = edge_info["edge"] * 100
    # Use actual best book odds if available, otherwise derive from market prob
    if best_odds is not None:
        market_odds = int(best_odds)
    else:
        market_odds = prob_to_american(edge_info['market_prob'])

    # --- Lead: core thesis ---
    if market_odds > 130:
        parts.append(f"The market has {team} as a {market_odds:+.0f} underdog, but our simulation disagrees")
    elif market_odds > 0:
        parts.append(f"{team} is getting plus-money at {market_odds:+.0f} and the model thinks that's too cheap")
    elif elo_prob > 0.58:
        parts.append(f"{team} is the better team by Elo and the sim confirms it — the market isn't giving them enough credit")
    elif elo_prob < 0.45 and model_prob > 0.50:
        parts.append(f"Elo has {team} as the weaker club, but the matchup-level simulation tells a different story today")
    else:
        parts.append(f"Our model sees a {edge_pct:.0f}% edge on {team} that the books are missing")

    # --- Pitching matchup ---
    if team_pitcher and opp_pitcher:
        if market_odds > 100:
            parts.append(f"{team_pitcher} keeps {team} competitive on the mound against {opp_pitcher}")
        elif market_odds < -150:
            parts.append(f"{team_pitcher} is a big pitching edge over {opp_pitcher}")
        else:
            parts.append(f"{team_pitcher} toes the rubber against {opp_pitcher}")

    # --- Sim run scoring ---
    if sim_detail:
        team_runs = sim_detail.get("avg_home_runs" if side == "home" else "avg_away_runs", 0)
        opp_runs = sim_detail.get("avg_away_runs" if side == "home" else "avg_home_runs", 0)
        run_diff = team_runs - opp_runs
        if team_runs >= 5.0:
            parts.append(f"The sim projects {team} to plate {team_runs:.1f} runs — expect some fireworks")
        elif opp_runs <= 3.0 and team_pitcher:
            parts.append(f"{team_pitcher} and the pen should hold {opponent} to around {opp_runs:.1f} runs")
        if run_diff >= 1.5:
            parts.append(f"Projected to outscore {opponent} by {run_diff:.1f} runs")
        elif run_diff >= 0.8:
            parts.append(f"Slight run-scoring edge of {run_diff:.1f} runs in the sim")

    # --- Environment color ---
    hr_factor = park.get("HR", 1.0)
    if weather and weather.get("temperature") and weather["temperature"] >= 85:
        parts.append("Heat cranks up the offense")
    elif weather and weather.get("temperature") and weather["temperature"] <= 50:
        parts.append("Cold temps suppress the bats")
    if weather and weather.get("wind_speed", 0) >= 12:
        wd = weather.get("wind_direction", "")
        if "out" in wd:
            parts.append("Wind blowing out — balls are carrying")
        elif "in" in wd:
            parts.append("Wind blowing in — pitchers' day")
    if hr_factor >= 1.15:
        parts.append("Hitter-friendly yard boosts power")
    elif hr_factor <= 0.88:
        parts.append("Pitcher's park suppresses the long ball")

    # --- Elo gap ---
    if elo_rating and opp_elo_rating:
        gap = elo_rating - opp_elo_rating
        if gap >= 60:
            parts.append(f"{team} holds a commanding {gap:.0f}-point Elo edge")
        elif gap <= -60:
            parts.append(f"{opponent} is the better team on paper — but the matchup favors {team} today")

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
    parser.add_argument("--mode", choices=["preview", "early", "late"], default="late",
                        help="Run mode: preview (RotoGrinders projected), early (day-of projected), late (confirmed)")
    args = parser.parse_args()

    bankroll = args.bankroll
    if bankroll is None:
        state = load_state()
        bankroll = state["bankroll"] if state else 1000.0

    run_daily(bankroll=bankroll, date=args.date, n_sims=args.sims,
              include_spring=args.spring, mode=args.mode)
