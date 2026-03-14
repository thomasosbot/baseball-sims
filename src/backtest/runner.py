"""
Backtest runner: evaluate the model against historical seasons.

v0.1 — League-average profiles. Baseline. Brier = 0.2533.
v0.2 — Real lineups extracted from Statcast + real player profiles.
v0.3 — Historical odds integration: CLV, ROI, simulated P&L.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
from tqdm import tqdm

from config import (
    BACKTEST_START_YEAR, BACKTEST_END_YEAR, N_SIMULATIONS,
    CACHE_DIR, KELLY_FRACTION, MAX_BET_FRACTION,
    PRIOR_YEAR_WEIGHT, MARCEL_EFFECTIVE_PA,
    ML_ALPHA, ML_MIN_EDGE, ML_MAX_EDGE, ML_MIN_CONFIDENCE,
    TOTALS_ALPHA, TOTALS_MIN_EDGE, TOTALS_MAX_EDGE, TOTALS_MIN_CONFIDENCE,
    HOME_FIELD_ADVANTAGE, ELO_BLEND_WEIGHT,
)
from src.data.fetch import (
    fetch_statcast_season, fetch_season_schedule, team_abbrev,
    TEAM_NAME_TO_ABBREV,
)
from src.data.process import (
    aggregate_batter_rates,
    aggregate_pitcher_rates,
    extract_game_lineups,
    extract_batter_handedness,
    prepare_for_rolling,
    extract_team_relievers,
    aggregate_team_bullpen_rates,
)
from src.data.cumulative import CumulativeStats
from src.features.marcel import project_marcel, blend_bhq_marcel
from src.data.bhq import load_bhq_hitters, load_bhq_pitchers
from src.features.bhq_rates import convert_bhq_hitters, convert_bhq_pitchers
from src.features.elo import EloRatings, build_preseason_elo
from src.features.batting import build_batter_profile
from src.features.pitching import build_pitcher_profile, build_bullpen_profile
from src.features.park_factors import get_park_factors
from src.simulation.constants import LEAGUE_RATES
from src.simulation.game_sim import monte_carlo_win_probability
from src.betting.odds import american_to_prob, american_to_decimal, remove_vig
from src.betting.edge import calculate_edge, compute_game_confidence
from src.betting.kelly import size_bet


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


def run_backtest(
    start_year: int = BACKTEST_START_YEAR,
    end_year: int = BACKTEST_END_YEAR,
    n_sims: int = N_SIMULATIONS,
    max_games_per_year: Optional[int] = None,
    output_path: Optional[Path] = None,
    use_statcast: bool = True,
    bankroll: float = 10_000.0,
) -> pd.DataFrame:
    """
    Main backtest loop.  For each historical game:
      1. Look up actual starting lineup + starter from Statcast
      2. Build player profiles from season-long Statcast data
      3. Run Monte Carlo simulation
      4. Compare prediction to actual result
      5. If historical odds available, simulate bets and track P&L
    """
    all_results = []

    for year in range(start_year, end_year + 1):
        print(f"\n{'=' * 50}")
        print(f"  Backtesting {year}")
        print(f"{'=' * 50}")

        batter_speeds = {}  # populated from BHQ SPD in rolling backtest

        # Load schedule (for actual game results)
        schedule = fetch_season_schedule(year)
        schedule_games = _schedule_to_games(schedule)
        schedule_lookup = {g["game_id"]: g for g in schedule_games}

        # Try to load historical odds
        closing_lines = _load_closing_lines(year)
        if closing_lines is not None:
            print(f"  Loaded {len(closing_lines)} closing lines for odds comparison")
        else:
            print("  No historical odds available (run: python scripts/fetch_data.py --odds)")

        closing_totals = _load_closing_totals(year)
        if closing_totals is not None:
            print(f"  Loaded {len(closing_totals)} closing totals lines")

        if not use_statcast:
            # Fallback: league-average profiles, no lineups
            games = schedule_games[:max_games_per_year] if max_games_per_year else schedule_games
            print(f"  Simulating {len(games)} games (league-avg profiles)...")
            for g in tqdm(games, desc=f"  {year}"):
                try:
                    pred = _sim_league_avg(g, n_sims)
                    pred["year"] = year
                    _attach_odds(pred, closing_lines, bankroll)
                    _attach_totals(pred, closing_totals, pred.pop("_total_runs_dist", None), bankroll)
                    all_results.append(pred)
                except Exception:
                    continue
            continue

        # Load Statcast and build everything
        print("  Loading Statcast data...")
        statcast = fetch_statcast_season(year)

        print("  Building player profiles...")
        batter_rates   = aggregate_batter_rates(statcast, min_pa=50)
        pitcher_rates  = aggregate_pitcher_rates(statcast, min_bf=50)
        batter_profiles  = _build_batter_profiles(batter_rates)
        pitcher_profiles = _build_pitcher_profiles(pitcher_rates)
        batter_hands     = extract_batter_handedness(statcast)

        print("  Building team bullpen profiles...")
        reliever_info = extract_team_relievers(statcast)
        team_bullpen_rates = aggregate_team_bullpen_rates(statcast, reliever_info)
        team_bullpen_profiles = {
            team: build_bullpen_profile(df) for team, df in team_bullpen_rates.items()
        }
        print(f"    {len(batter_profiles)} batters, {len(pitcher_profiles)} pitchers, "
              f"{len(team_bullpen_profiles)} team bullpens")

        print("  Extracting lineups from Statcast...")
        lineup_df = extract_game_lineups(statcast)
        lineup_lookup = {int(row["game_pk"]): row for _, row in lineup_df.iterrows()}
        print(f"    {len(lineup_lookup)} games with lineup data")

        # Match schedule games to lineup data
        matched = []
        for g in schedule_games:
            gid = g["game_id"]
            if gid in lineup_lookup:
                g["lineup_data"] = lineup_lookup[gid]
                matched.append(g)

        if max_games_per_year:
            matched = matched[:max_games_per_year]

        print(f"  Simulating {len(matched)} games ({n_sims} sims each)...")
        for g in tqdm(matched, desc=f"  {year}"):
            try:
                pred = _sim_with_lineups(
                    g, g["lineup_data"],
                    batter_profiles, pitcher_profiles, batter_hands,
                    n_sims,
                    team_bullpen_profiles=team_bullpen_profiles,
                    batter_speeds=batter_speeds,
                )
                pred["year"] = year
                _attach_odds(pred, closing_lines, bankroll)
                _attach_totals(pred, closing_totals, pred.pop("_total_runs_dist", None), bankroll)
                all_results.append(pred)
            except Exception:
                continue

    df = pd.DataFrame(all_results)
    if output_path and not df.empty:
        for col in ("bet_won", "totals_bet_won"):
            if col in df.columns:
                df[col] = df[col].apply(lambda x: int(x) if x is not None and not pd.isna(x) else x)
        df.to_csv(output_path, index=False)
        print(f"\nSaved {len(df)} predictions to {output_path}")

    return df


def run_rolling_backtest(
    start_year: int = BACKTEST_START_YEAR,
    end_year: int = BACKTEST_END_YEAR,
    n_sims: int = N_SIMULATIONS,
    max_games_per_year: Optional[int] = None,
    output_path: Optional[Path] = None,
    bankroll: float = 10_000.0,
) -> pd.DataFrame:
    """
    Rolling-window backtest: builds player profiles cumulatively so each
    game is predicted using only data available before that date.

    For each date in the season:
      1. Snapshot cumulative stats → build profiles (pre-game knowledge only)
      2. Simulate all games on this date
      3. Update cumulative stats with this date's PA outcomes
    """
    all_results = []

    for year in range(start_year, end_year + 1):
        print(f"\n{'=' * 50}")
        print(f"  Rolling Backtest {year}")
        print(f"{'=' * 50}")

        # Load data
        schedule = fetch_season_schedule(year)
        schedule_games = _schedule_to_games(schedule)
        schedule_lookup = {g["game_id"]: g for g in schedule_games}

        closing_lines = _load_closing_lines(year)
        if closing_lines is not None:
            print(f"  Loaded {len(closing_lines)} closing lines for odds comparison")

        closing_totals = _load_closing_totals(year)
        if closing_totals is not None:
            print(f"  Loaded {len(closing_totals)} closing totals lines")

        print("  Loading Statcast data...")
        statcast = fetch_statcast_season(year)

        # Extract lineups (known pre-game, no look-ahead issue)
        print("  Extracting lineups from Statcast...")
        lineup_df = extract_game_lineups(statcast)
        lineup_lookup = {int(row["game_pk"]): row for _, row in lineup_df.iterrows()}
        print(f"    {len(lineup_lookup)} games with lineup data")

        # Prepare PA events for day-by-day ingestion
        print("  Preparing rolling PA data...")
        rolling_pa = prepare_for_rolling(statcast)
        pa_by_date = dict(list(rolling_pa.groupby("game_date")))
        dates = sorted(pa_by_date.keys())
        print(f"    {len(dates)} game dates, {len(rolling_pa)} total PAs")

        # Match schedule games to lineup data, group by date
        games_by_date = {}
        game_count = 0
        for g in schedule_games:
            gid = g["game_id"]
            if gid not in lineup_lookup:
                continue
            g["lineup_data"] = lineup_lookup[gid]
            game_date = str(g["date"])[:10]
            games_by_date.setdefault(game_date, []).append(g)
            game_count += 1

        # Initialize cumulative tracker, seeded with Marcel projections
        cumulative = CumulativeStats()
        batter_speeds = {}  # {mlbamid: SPD} for stolen base model
        marcel_years = {}
        for prior_yr in range(year - 3, year):
            prior_cache = CACHE_DIR / f"statcast_{prior_yr}.pkl"
            if prior_cache.exists():
                marcel_years[prior_yr] = fetch_statcast_season(prior_yr)

        if marcel_years:
            available = sorted(marcel_years.keys())
            print(f"  Building Marcel projections from {available} ...")
            batter_proj, pitcher_proj = project_marcel(marcel_years, year)

            # Blend with BHQ skills-based rates if available
            from config import BHQ_BLEND_WEIGHT
            if BHQ_BLEND_WEIGHT > 0:
                # Use most recent prior year's BHQ data (no look-ahead)
                bhq_year = year - 1
                bhq_h = load_bhq_hitters(bhq_year)
                bhq_p = load_bhq_pitchers(bhq_year)
                if not bhq_h.empty or not bhq_p.empty:
                    bhq_h_rates = convert_bhq_hitters(bhq_h) if not bhq_h.empty else {}
                    bhq_p_rates = convert_bhq_pitchers(bhq_p) if not bhq_p.empty else {}
                    batter_proj, pitcher_proj = blend_bhq_marcel(
                        batter_proj, pitcher_proj, bhq_h_rates, bhq_p_rates
                    )
                # Extract speed scores for stolen base model
                if not bhq_h.empty and "SPD" in bhq_h.columns:
                    for mlbamid, row in bhq_h.iterrows():
                        spd = row.get("SPD")
                        if pd.notna(spd) and spd > 0:
                            batter_speeds[int(mlbamid)] = float(spd)

            cumulative.init_from_marcel(batter_proj, pitcher_proj,
                                        effective_pa=MARCEL_EFFECTIVE_PA)
            print(f"    {cumulative.num_batters} batters, {cumulative.num_pitchers} pitchers seeded")
            del marcel_years, batter_proj, pitcher_proj
        else:
            print(f"  No prior Statcast cached — starting cold (pure league avg)")

        # Build preseason Elo from prior seasons' results
        elo_prior_games = {}
        for prior_yr in range(year - 3, year):
            prior_sched_cache = CACHE_DIR / f"schedule_{prior_yr}.pkl"
            if prior_sched_cache.exists():
                prior_sched = fetch_season_schedule(prior_yr)
                elo_prior_games[prior_yr] = _schedule_to_games(prior_sched)
        if elo_prior_games:
            elo = build_preseason_elo(elo_prior_games)
            print(f"  Elo seeded from {sorted(elo_prior_games.keys())} (spread={elo.spread:.0f})")
            del elo_prior_games
        else:
            elo = EloRatings()
            print(f"  Elo starting fresh (all teams at 1500)")

        games_simulated = 0

        print(f"  Simulating {game_count} games across {len(dates)} dates ({n_sims} sims each)...")
        for date in tqdm(dates, desc=f"  {year} dates"):
            date_str = str(date)[:10]

            # 1. Snapshot: build profiles from cumulative stats BEFORE today
            batter_rates = cumulative.to_batter_rates_df()
            pitcher_rates = cumulative.to_pitcher_rates_df()
            batter_profiles = _build_batter_profiles(batter_rates) if not batter_rates.empty else {}
            pitcher_profiles = _build_pitcher_profiles(pitcher_rates) if not pitcher_rates.empty else {}
            batter_hands = cumulative.get_batter_handedness()

            # Build team bullpen profiles from cumulative reliever data
            team_reliever_rates = cumulative.get_team_reliever_rates()
            team_bullpen_profiles = {
                team: build_bullpen_profile(df) for team, df in team_reliever_rates.items()
            }

            # 2. Simulate all games on this date
            todays_games = games_by_date.get(date_str, [])
            for g in todays_games:
                if max_games_per_year and games_simulated >= max_games_per_year:
                    break
                try:
                    elo_prob = elo.expected_win_prob(g["home_team"], g["away_team"])
                    pred = _sim_with_lineups(
                        g, g["lineup_data"],
                        batter_profiles, pitcher_profiles, batter_hands,
                        n_sims,
                        team_bullpen_profiles=team_bullpen_profiles,
                        elo_home_prob=elo_prob,
                        batter_speeds=batter_speeds,
                    )
                    pred["year"] = year
                    pred["cumulative_batters"] = cumulative.num_batters
                    pred["cumulative_pitchers"] = cumulative.num_pitchers
                    _attach_odds(pred, closing_lines, bankroll)
                    _attach_totals(pred, closing_totals, pred.pop("_total_runs_dist", None), bankroll)
                    all_results.append(pred)
                    games_simulated += 1
                except Exception:
                    continue

            if max_games_per_year and games_simulated >= max_games_per_year:
                break

            # 3. Update Elo with today's actual results
            for g in todays_games:
                elo.update(g["home_team"], g["away_team"], g["home_win"])

            # 4. Update cumulative stats with today's PA outcomes
            #    Also identify relievers from today's games for bullpen profiles
            if date in pa_by_date:
                day_pa = pa_by_date[date]
                cumulative.update_from_day(day_pa)

                # Identify starters/relievers from today's PA data
                for game_pk, gdf in day_pa.groupby("game_pk"):
                    gdf = gdf.sort_values(gdf.columns[0])  # preserve order
                    home_team = gdf["home_team"].iloc[0] if "home_team" in gdf.columns else None
                    away_team = gdf["away_team"].iloc[0] if "away_team" in gdf.columns else None

                    if home_team and "inning_topbot" in gdf.columns:
                        # Top of inning: home pitchers
                        top = gdf[gdf["inning_topbot"] == "Top"]
                        home_pitchers = list(dict.fromkeys(top["pitcher"]))
                        for i, pid in enumerate(home_pitchers):
                            if i > 0:
                                cumulative.register_reliever(int(pid), home_team)

                        # Bot of inning: away pitchers
                        bot = gdf[gdf["inning_topbot"] == "Bot"]
                        away_pitchers = list(dict.fromkeys(bot["pitcher"]))
                        for i, pid in enumerate(away_pitchers):
                            if i > 0 and away_team:
                                cumulative.register_reliever(int(pid), away_team)

        print(f"  Completed: {games_simulated} games, "
              f"{cumulative.num_batters} batters, {cumulative.num_pitchers} pitchers tracked")

    df = pd.DataFrame(all_results)
    if output_path and not df.empty:
        for col in ("bet_won", "totals_bet_won"):
            if col in df.columns:
                df[col] = df[col].apply(lambda x: int(x) if x is not None and not pd.isna(x) else x)
        df.to_csv(output_path, index=False)
        print(f"\nSaved {len(df)} predictions to {output_path}")

    return df


# ---------------------------------------------------------------------------
# Profile builders
# ---------------------------------------------------------------------------

def _build_batter_profiles(batter_rates: pd.DataFrame) -> dict:
    """Build profiles keyed by MLBAM batter ID (int)."""
    profiles = {}
    for _, row in batter_rates.iterrows():
        pid = int(row["batter_id"])
        try:
            profiles[pid] = build_batter_profile(row)
        except Exception:
            continue
    return profiles


def _build_pitcher_profiles(pitcher_rates: pd.DataFrame) -> dict:
    """Build profiles keyed by MLBAM pitcher ID (int)."""
    profiles = {}
    for _, row in pitcher_rates.iterrows():
        pid = int(row["pitcher_id"])
        try:
            prof = build_pitcher_profile(row)
            profiles[pid] = prof
        except Exception:
            continue
    return profiles


# ---------------------------------------------------------------------------
# Game simulation with real lineups
# ---------------------------------------------------------------------------

def _sim_with_lineups(
    game: dict,
    lineup_data,
    batter_profiles: dict,
    pitcher_profiles: dict,
    batter_hands: dict,
    n_sims: int,
    team_bullpen_profiles: dict = None,
    elo_home_prob: float = None,
    batter_speeds: dict = None,
) -> dict:
    """Simulate a game using real starting lineups and pitcher profiles."""
    park = get_park_factors(game["home_team"])

    # Build lineups (with speed scores for stolen base model)
    home_lineup = _build_lineup(lineup_data["home_lineup"], batter_profiles, batter_hands, batter_speeds)
    away_lineup = _build_lineup(lineup_data["away_lineup"], batter_profiles, batter_hands, batter_speeds)

    # Build pitcher profiles
    home_starter = _get_pitcher(lineup_data["home_starter_id"], pitcher_profiles)
    away_starter = _get_pitcher(lineup_data["away_starter_id"], pitcher_profiles)

    # Team-specific bullpen profiles, falling back to league average
    if team_bullpen_profiles:
        home_bullpen = team_bullpen_profiles.get(game["home_team"], _DEFAULT_PITCHER.copy())
        away_bullpen = team_bullpen_profiles.get(game["away_team"], _DEFAULT_PITCHER.copy())
    else:
        home_bullpen = _DEFAULT_PITCHER.copy()
        away_bullpen = _DEFAULT_PITCHER.copy()

    result = monte_carlo_win_probability(
        home_lineup=home_lineup,
        away_lineup=away_lineup,
        home_starter=home_starter,
        away_starter=away_starter,
        home_bullpen=home_bullpen,
        away_bullpen=away_bullpen,
        park_factors=park,
        n_simulations=n_sims,
    )

    # Count how many real profiles we used (vs league-avg fallbacks)
    home_real = sum(1 for b in home_lineup if b is not _DEFAULT_BATTER)
    away_real = sum(1 for b in away_lineup if b is not _DEFAULT_BATTER)

    # Apply home field advantage
    raw_home = result["home_win_prob"]
    sim_home = min(raw_home + HOME_FIELD_ADVANTAGE, 0.99)

    # Blend simulation probability with Elo team-strength probability
    if elo_home_prob is not None and ELO_BLEND_WEIGHT > 0:
        adj_home = (1 - ELO_BLEND_WEIGHT) * sim_home + ELO_BLEND_WEIGHT * elo_home_prob
        adj_home = max(0.01, min(0.99, adj_home))
    else:
        adj_home = sim_home

    return {
        "game_id":             game["game_id"],
        "date":                game["date"],
        "home_team":           game["home_team"],
        "away_team":           game["away_team"],
        "home_name":           game.get("home_name", ""),
        "away_name":           game.get("away_name", ""),
        "model_home_win_prob": adj_home,
        "model_away_win_prob": 1.0 - adj_home,
        "avg_total_runs":      result["avg_total_runs"],
        "std_total_runs":      result["std_total_runs"],
        "actual_home_win":     game["home_win"],
        "actual_home_score":   game.get("home_score"),
        "actual_away_score":   game.get("away_score"),
        "sim_home_prob":       sim_home,
        "elo_home_prob":       elo_home_prob,
        "home_real_profiles":  home_real,
        "away_real_profiles":  away_real,
        "home_starter_id":     lineup_data.get("home_starter_id"),
        "away_starter_id":     lineup_data.get("away_starter_id"),
        "_total_runs_dist":    result["total_runs_dist"],  # temp, stripped before CSV
    }


def _build_lineup(lineup_ids, batter_profiles: dict, batter_hands: dict,
                   batter_speeds: dict = None) -> list:
    """
    Build a 9-batter lineup from MLBAM IDs.
    Falls back to league average for any batter not in our profiles.
    Includes BHQ speed score (SPD) for stolen base model.
    """
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

    # Pad to 9 if lineup is short
    while len(lineup) < 9:
        lineup.append(_DEFAULT_BATTER)

    return lineup


def _get_pitcher(pitcher_id, pitcher_profiles: dict) -> dict:
    """Look up a pitcher profile, falling back to league average."""
    if pitcher_id is None:
        return _DEFAULT_PITCHER.copy()
    prof = pitcher_profiles.get(int(pitcher_id))
    if prof is None:
        return _DEFAULT_PITCHER.copy()
    return prof


# ---------------------------------------------------------------------------
# Fallback: league-average simulation (baseline)
# ---------------------------------------------------------------------------

def _sim_league_avg(game: dict, n_sims: int) -> dict:
    park = get_park_factors(game["home_team"])
    lineup = [_DEFAULT_BATTER.copy() for _ in range(9)]
    result = monte_carlo_win_probability(
        home_lineup=lineup, away_lineup=lineup,
        home_starter=_DEFAULT_PITCHER.copy(),
        away_starter=_DEFAULT_PITCHER.copy(),
        home_bullpen=_DEFAULT_PITCHER.copy(),
        away_bullpen=_DEFAULT_PITCHER.copy(),
        park_factors=park,
        n_simulations=n_sims,
    )
    # Apply home field advantage
    raw_home = result["home_win_prob"]
    adj_home = min(raw_home + HOME_FIELD_ADVANTAGE, 0.99)

    return {
        "game_id":             game["game_id"],
        "date":                game["date"],
        "home_team":           game["home_team"],
        "away_team":           game["away_team"],
        "home_name":           game.get("home_name", ""),
        "away_name":           game.get("away_name", ""),
        "model_home_win_prob": adj_home,
        "model_away_win_prob": 1.0 - adj_home,
        "avg_total_runs":      result["avg_total_runs"],
        "std_total_runs":      result["std_total_runs"],
        "actual_home_win":     game["home_win"],
        "actual_home_score":   game.get("home_score"),
        "actual_away_score":   game.get("away_score"),
        "home_real_profiles":  0,
        "away_real_profiles":  0,
        "_total_runs_dist":    result["total_runs_dist"],
    }


def _schedule_to_games(schedule: pd.DataFrame) -> list:
    games = []
    for _, row in schedule.iterrows():
        status = str(row.get("status", ""))
        if "Final" not in status:
            continue
        home_score = int(row.get("home_score", 0) or 0)
        away_score = int(row.get("away_score", 0) or 0)
        games.append({
            "game_id":    row["game_id"],
            "date":       row.get("game_date", ""),
            "home_team":  team_abbrev(row["home_name"]),
            "away_team":  team_abbrev(row["away_name"]),
            "home_name":  row["home_name"],
            "away_name":  row["away_name"],
            "home_score": home_score,
            "away_score": away_score,
            "home_win":   home_score > away_score,
        })
    return games


# ---------------------------------------------------------------------------
# Historical odds integration
# ---------------------------------------------------------------------------

def _load_closing_lines(year: int) -> Optional[pd.DataFrame]:
    """Load pre-built closing lines for a season, if available."""
    path = CACHE_DIR / f"closing_lines_{year}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    return None


def _load_closing_totals(year: int) -> Optional[pd.DataFrame]:
    """Load pre-built closing totals for a season, if available."""
    path = CACHE_DIR / f"closing_totals_{year}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    return None


def _match_odds(pred: dict, closing_lines: pd.DataFrame) -> Optional[pd.Series]:
    """
    Match a game prediction to its closing line.
    Uses date + team names (The Odds API uses full names like 'New York Yankees').
    """
    if closing_lines is None or closing_lines.empty:
        return None

    date = str(pred.get("date", ""))[:10]
    home_full = pred.get("home_name", "")
    away_full = pred.get("away_name", "")

    # Try exact match on date + full team names
    mask = (
        (closing_lines["game_date"] == date)
        & (closing_lines["home_team_full"] == home_full)
        & (closing_lines["away_team_full"] == away_full)
    )
    matches = closing_lines[mask]

    if matches.empty:
        # Fuzzy fallback: try matching by abbreviation in the odds team names
        home_abbrev = pred.get("home_team", "")
        away_abbrev = pred.get("away_team", "")
        # The Odds API uses city-based names; try substring match
        mask = closing_lines["game_date"] == date
        day_games = closing_lines[mask]
        for _, row in day_games.iterrows():
            odds_home = row["home_team_full"]
            odds_away = row["away_team_full"]
            # Check if our team abbreviation maps to the odds team name
            if (TEAM_NAME_TO_ABBREV.get(odds_home) == home_abbrev
                    and TEAM_NAME_TO_ABBREV.get(odds_away) == away_abbrev):
                return row
        return None

    return matches.iloc[0]


def _attach_odds(pred: dict, closing_lines: Optional[pd.DataFrame], bankroll: float):
    """
    If historical odds are available, attach market data and simulate bets.
    Mutates the pred dict in-place with odds and bet fields.
    """
    if closing_lines is None:
        return

    odds_row = _match_odds(pred, closing_lines)
    if odds_row is None:
        return

    model_home = pred["model_home_win_prob"]  # already includes HFA
    model_away = pred["model_away_win_prob"]
    market_home_nv = odds_row["home_no_vig_prob"]
    market_away_nv = odds_row["away_no_vig_prob"]
    best_home_odds = odds_row["best_home_odds"]
    best_away_odds = odds_row["best_away_odds"]

    # Compute game confidence (uses cumulative_pitchers from rolling backtest)
    # Pass post-HFA model_home so agreement check compares apples-to-apples with market
    cumulative_pitchers = pred.get("cumulative_pitchers", 1300)  # full backtest defaults to max
    confidence = compute_game_confidence(
        cumulative_pitchers=cumulative_pitchers,
        model_prob=model_home,
        market_prob=market_home_nv,
    )
    pred["confidence"] = confidence

    # Store market data
    pred["market_home_nv_prob"] = market_home_nv
    pred["market_away_nv_prob"] = market_away_nv
    pred["best_home_odds"] = best_home_odds
    pred["best_away_odds"] = best_away_odds
    pred["best_home_book"] = odds_row.get("best_home_book", "")
    pred["best_away_book"] = odds_row.get("best_away_book", "")
    pred["pinnacle_home"] = odds_row.get("pinnacle_home")
    pred["pinnacle_away"] = odds_row.get("pinnacle_away")
    pred["vig"] = odds_row.get("vig")

    # Calculate edge for both sides (with alpha shrinkage + confidence)
    home_edge_info = calculate_edge(model_home, market_home_nv, best_home_odds, confidence, alpha=ML_ALPHA)
    away_edge_info = calculate_edge(model_away, market_away_nv, best_away_odds, confidence, alpha=ML_ALPHA)

    pred["home_edge"] = home_edge_info["edge"]
    pred["away_edge"] = away_edge_info["edge"]
    pred["home_ev"] = home_edge_info["ev_per_unit"]
    pred["away_ev"] = away_edge_info["ev_per_unit"]

    # Determine best bet (if any)
    pred["bet_side"] = None
    pred["bet_odds"] = None
    pred["bet_edge"] = None
    pred["bet_fraction"] = 0.0
    pred["bet_stake"] = 0.0
    pred["bet_profit"] = 0.0
    pred["bet_won"] = None

    # Check home side
    if (ML_MIN_EDGE <= home_edge_info["edge"] <= ML_MAX_EDGE
            and home_edge_info["ev_per_unit"] > 0
            and confidence >= ML_MIN_CONFIDENCE):
        sizing = size_bet(home_edge_info["adjusted_prob"], american_to_decimal(best_home_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            won = pred["actual_home_win"]
            decimal_odds = american_to_decimal(best_home_odds)
            profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

            pred["bet_side"] = "home"
            pred["bet_odds"] = best_home_odds
            pred["bet_edge"] = home_edge_info["edge"]
            pred["bet_fraction"] = sizing["bet_fraction"]
            pred["bet_stake"] = sizing["bet_dollars"]
            pred["bet_profit"] = round(profit, 2)
            pred["bet_won"] = won

    # Check away side (only if no home bet, to avoid hedging against ourselves)
    if pred["bet_side"] is None:
        if (ML_MIN_EDGE <= away_edge_info["edge"] <= ML_MAX_EDGE
                and away_edge_info["ev_per_unit"] > 0
                and confidence >= ML_MIN_CONFIDENCE):
            sizing = size_bet(away_edge_info["adjusted_prob"], american_to_decimal(best_away_odds),
                              bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
            if sizing["bet_dollars"] > 0:
                won = not pred["actual_home_win"]
                decimal_odds = american_to_decimal(best_away_odds)
                profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

                pred["bet_side"] = "away"
                pred["bet_odds"] = best_away_odds
                pred["bet_edge"] = away_edge_info["edge"]
                pred["bet_fraction"] = sizing["bet_fraction"]
                pred["bet_stake"] = sizing["bet_dollars"]
                pred["bet_profit"] = round(profit, 2)
                pred["bet_won"] = won


def _match_totals(pred: dict, closing_totals: pd.DataFrame) -> Optional[pd.Series]:
    """Match a game prediction to its closing totals line."""
    if closing_totals is None or closing_totals.empty:
        return None

    date = str(pred.get("date", ""))[:10]
    home_full = pred.get("home_name", "")
    away_full = pred.get("away_name", "")

    mask = (
        (closing_totals["game_date"] == date)
        & (closing_totals["home_team_full"] == home_full)
        & (closing_totals["away_team_full"] == away_full)
    )
    matches = closing_totals[mask]

    if matches.empty:
        home_abbrev = pred.get("home_team", "")
        away_abbrev = pred.get("away_team", "")
        mask = closing_totals["game_date"] == date
        day_games = closing_totals[mask]
        for _, row in day_games.iterrows():
            if (TEAM_NAME_TO_ABBREV.get(row["home_team_full"]) == home_abbrev
                    and TEAM_NAME_TO_ABBREV.get(row["away_team_full"]) == away_abbrev):
                return row
        return None

    return matches.iloc[0]


def _attach_totals(
    pred: dict,
    closing_totals: Optional[pd.DataFrame],
    total_runs_dist,
    bankroll: float,
):
    """
    If historical totals odds are available, compute over/under probability
    from the simulation distribution and simulate totals bets.
    """
    if closing_totals is None or total_runs_dist is None:
        return

    totals_row = _match_totals(pred, closing_totals)
    if totals_row is None:
        return

    import numpy as np

    line = totals_row["total_line"]
    over_nv = totals_row["over_no_vig_prob"]
    under_nv = totals_row["under_no_vig_prob"]
    best_over_odds = totals_row["best_over_odds"]
    best_under_odds = totals_row["best_under_odds"]

    # NOTE: park runs factor removed — component park factors (HR/1B/2B/3B/BB/K)
    # already adjust PA outcomes in the simulation, so the total_runs_dist already
    # reflects park effects. Applying the runs factor on top was double-counting.

    # Compute model over/under probabilities from sim distribution
    # Games exactly on the line are a push (neither over nor under)
    over_count = np.sum(total_runs_dist > line)
    under_count = np.sum(total_runs_dist < line)
    decided = over_count + under_count
    if decided == 0:
        return

    model_over_prob = float(over_count / decided)
    model_under_prob = float(under_count / decided)

    # Store totals market data
    pred["total_line"] = line
    pred["model_over_prob"] = model_over_prob
    pred["model_under_prob"] = model_under_prob
    pred["market_over_nv_prob"] = over_nv
    pred["market_under_nv_prob"] = under_nv
    pred["best_over_odds"] = best_over_odds
    pred["best_under_odds"] = best_under_odds
    pred["best_over_book"] = totals_row.get("best_over_book", "")
    pred["best_under_book"] = totals_row.get("best_under_book", "")

    # Calculate edges (totals-specific alpha, same confidence as moneyline)
    confidence = pred.get("confidence", 1.0)
    over_edge_info = calculate_edge(model_over_prob, over_nv, best_over_odds, confidence, alpha=TOTALS_ALPHA)
    under_edge_info = calculate_edge(model_under_prob, under_nv, best_under_odds, confidence, alpha=TOTALS_ALPHA)

    pred["over_edge"] = over_edge_info["edge"]
    pred["under_edge"] = under_edge_info["edge"]

    # Totals bet defaults
    pred["totals_bet_side"] = None
    pred["totals_bet_odds"] = None
    pred["totals_bet_edge"] = None
    pred["totals_bet_fraction"] = 0.0
    pred["totals_bet_stake"] = 0.0
    pred["totals_bet_profit"] = 0.0
    pred["totals_bet_won"] = None

    actual_total = pred.get("actual_home_score", 0) + pred.get("actual_away_score", 0)

    # Check over
    if (TOTALS_MIN_EDGE <= over_edge_info["edge"] <= TOTALS_MAX_EDGE
            and over_edge_info["ev_per_unit"] > 0
            and confidence >= TOTALS_MIN_CONFIDENCE):
        sizing = size_bet(over_edge_info["adjusted_prob"], american_to_decimal(best_over_odds),
                          bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
        if sizing["bet_dollars"] > 0:
            if actual_total == line:
                won = None  # push
                profit = 0.0
            else:
                won = actual_total > line
                decimal_odds = american_to_decimal(best_over_odds)
                profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

            pred["totals_bet_side"] = "over"
            pred["totals_bet_odds"] = best_over_odds
            pred["totals_bet_edge"] = over_edge_info["edge"]
            pred["totals_bet_fraction"] = sizing["bet_fraction"]
            pred["totals_bet_stake"] = sizing["bet_dollars"]
            pred["totals_bet_profit"] = round(profit, 2)
            pred["totals_bet_won"] = won

    # Check under (only if no over bet)
    if pred["totals_bet_side"] is None:
        if (TOTALS_MIN_EDGE <= under_edge_info["edge"] <= TOTALS_MAX_EDGE
                and under_edge_info["ev_per_unit"] > 0
                and confidence >= TOTALS_MIN_CONFIDENCE):
            sizing = size_bet(under_edge_info["adjusted_prob"], american_to_decimal(best_under_odds),
                              bankroll, KELLY_FRACTION, MAX_BET_FRACTION)
            if sizing["bet_dollars"] > 0:
                if actual_total == line:
                    won = None
                    profit = 0.0
                else:
                    won = actual_total < line
                    decimal_odds = american_to_decimal(best_under_odds)
                    profit = sizing["bet_dollars"] * (decimal_odds - 1) if won else -sizing["bet_dollars"]

                pred["totals_bet_side"] = "under"
                pred["totals_bet_odds"] = best_under_odds
                pred["totals_bet_edge"] = under_edge_info["edge"]
                pred["totals_bet_fraction"] = sizing["bet_fraction"]
                pred["totals_bet_stake"] = sizing["bet_dollars"]
                pred["totals_bet_profit"] = round(profit, 2)
                pred["totals_bet_won"] = won
