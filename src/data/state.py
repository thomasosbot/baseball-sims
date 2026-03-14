"""
State persistence for daily pipeline.

Saves and loads CumulativeStats, Elo ratings, batter speeds, and bankroll
between daily runs so that each day picks up where the previous left off.
"""

import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from config import CACHE_DIR, DATA_DIR, MARCEL_EFFECTIVE_PA, BHQ_BLEND_WEIGHT
from src.data.cumulative import CumulativeStats
from src.features.elo import EloRatings, build_preseason_elo

STATE_DIR = DATA_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)


def _state_path(date: str) -> Path:
    """Path for a given date's state file."""
    return STATE_DIR / f"state_{date}.pkl"


def _latest_state_path() -> Optional[Path]:
    """Find the most recent state file."""
    files = sorted(STATE_DIR.glob("state_*.pkl"))
    return files[-1] if files else None


def save_state(
    cumulative: CumulativeStats,
    elo: EloRatings,
    batter_speeds: dict,
    bankroll: float,
    date: str,
):
    """Save pipeline state to disk."""
    state = {
        "cumulative": cumulative,
        "elo": elo,
        "batter_speeds": batter_speeds,
        "bankroll": bankroll,
        "date": date,
        "saved_at": datetime.now().isoformat(),
    }
    path = _state_path(date)
    with open(path, "wb") as f:
        pickle.dump(state, f)
    print(f"  State saved to {path}")


def load_state(date: Optional[str] = None) -> Optional[dict]:
    """
    Load pipeline state. If date is specified, load that date.
    Otherwise load the most recent state.
    """
    if date:
        path = _state_path(date)
    else:
        path = _latest_state_path()

    if path is None or not path.exists():
        return None

    with open(path, "rb") as f:
        state = pickle.load(f)
    print(f"  State loaded from {path} (date={state['date']})")
    return state


def init_preseason(year: int) -> Tuple[CumulativeStats, EloRatings, dict]:
    """
    Build preseason state from Marcel projections + BHQ blend + Elo.

    This mirrors the seeding logic from run_rolling_backtest() but packages
    it for the daily pipeline.

    Returns (cumulative, elo, batter_speeds).
    """
    from src.data.fetch import fetch_statcast_season, fetch_season_schedule
    from src.data.bhq import load_bhq_hitters, load_bhq_pitchers
    from src.features.marcel import project_marcel, blend_bhq_marcel
    from src.features.bhq_rates import convert_bhq_hitters, convert_bhq_pitchers
    from src.backtest.runner import _schedule_to_games
    import pandas as pd

    # --- Marcel projections from 3 prior years ---
    marcel_years = {}
    for prior_yr in range(year - 3, year):
        cache_path = CACHE_DIR / f"statcast_{prior_yr}.pkl"
        if cache_path.exists():
            marcel_years[prior_yr] = fetch_statcast_season(prior_yr)

    if not marcel_years:
        raise RuntimeError(
            f"No Statcast data cached for years {year-3}-{year-1}. "
            f"Run: python scripts/fetch_data.py --years {year-3} {year-2} {year-1} --statcast"
        )

    available = sorted(marcel_years.keys())
    print(f"  Building Marcel projections from {available} ...")
    batter_proj, pitcher_proj = project_marcel(marcel_years, year)

    # --- Blend with BHQ skills rates ---
    batter_speeds = {}
    if BHQ_BLEND_WEIGHT > 0:
        bhq_year = year - 1
        bhq_h = load_bhq_hitters(bhq_year)
        bhq_p = load_bhq_pitchers(bhq_year)
        if not bhq_h.empty or not bhq_p.empty:
            bhq_h_rates = convert_bhq_hitters(bhq_h) if not bhq_h.empty else {}
            bhq_p_rates = convert_bhq_pitchers(bhq_p) if not bhq_p.empty else {}
            batter_proj, pitcher_proj = blend_bhq_marcel(
                batter_proj, pitcher_proj, bhq_h_rates, bhq_p_rates
            )
        # Extract speed scores
        if not bhq_h.empty and "SPD" in bhq_h.columns:
            for mlbamid, row in bhq_h.iterrows():
                spd = row.get("SPD")
                if pd.notna(spd) and spd > 0:
                    batter_speeds[int(mlbamid)] = float(spd)

    # --- Seed CumulativeStats ---
    cumulative = CumulativeStats()
    cumulative.init_from_marcel(batter_proj, pitcher_proj,
                                effective_pa=MARCEL_EFFECTIVE_PA)
    print(f"    {cumulative.num_batters} batters, {cumulative.num_pitchers} pitchers seeded")

    # --- Build Elo from prior seasons ---
    elo_prior_games = {}
    for prior_yr in range(year - 3, year):
        cache_path = CACHE_DIR / f"schedule_{prior_yr}.pkl"
        if cache_path.exists():
            prior_sched = fetch_season_schedule(prior_yr)
            elo_prior_games[prior_yr] = _schedule_to_games(prior_sched)

    if elo_prior_games:
        elo = build_preseason_elo(elo_prior_games)
        print(f"  Elo seeded from {sorted(elo_prior_games.keys())} (spread={elo.spread:.0f})")
    else:
        elo = EloRatings()
        print("  Elo starting fresh (all teams at 1500)")

    return cumulative, elo, batter_speeds
