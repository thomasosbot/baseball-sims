"""
Pitcher feature engineering.
Builds per-pitcher PA outcome profiles (rates allowed) with platoon splits.
"""

import numpy as np
import pandas as pd
from typing import Tuple

import config
from src.simulation.constants import LEAGUE_RATES

# Support both old single-scale and new split-scale configs
_PIT_SCALE = getattr(config, 'PITCHER_REGRESSION_SCALE', getattr(config, 'REGRESSION_SCALE', 0.40))

OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]

# Pitchers need larger samples to stabilise than batters.
REGRESSION_BF = {
    "K":   300,
    "BB":  400,
    "HBP": 800,
    "HR":  600,
    "3B":  1200,
    "2B":  700,
    "1B":  600,
    "OUT": 500,
}


def _regress(rate: float, n: int, league: float, weight: int) -> float:
    return (rate * n + league * weight) / (n + weight)


def build_pitcher_profile(pitcher_row: pd.Series) -> dict:
    """
    Build a pitcher profile with regressed PA outcome rates allowed.

    Returns {"L": {outcome: prob}, "R": {outcome: prob}}
    where "L" means "these are my rates when facing a LHB" etc.

    Also stores "throws" (L or R) so the simulation knows the pitcher's handedness.
    """
    total_bf = pitcher_row.get("total_bf", 400)
    throws   = pitcher_row.get("throws", "R")

    profile = {"throws": throws}
    for batter_hand in ("L", "R"):
        split_bf = pitcher_row.get(f"bf_vs{batter_hand}", total_bf)

        rates = {}
        for outcome in OUTCOMES:
            col_split   = f"rate_{outcome}_vs{batter_hand}"
            col_overall = f"rate_{outcome}"

            if col_split in pitcher_row.index and not np.isnan(pitcher_row.get(col_split, np.nan)):
                raw = pitcher_row[col_split]
                n   = split_bf
            elif col_overall in pitcher_row.index and not np.isnan(pitcher_row.get(col_overall, np.nan)):
                raw = pitcher_row[col_overall]
                n   = total_bf
            else:
                raw = LEAGUE_RATES[outcome]
                n   = 0

            rates[outcome] = _regress(raw, n, LEAGUE_RATES[outcome], int(REGRESSION_BF[outcome] * _PIT_SCALE))

        total = sum(rates.values())
        profile[batter_hand] = {k: v / total for k, v in rates.items()}

    return profile


def _weighted_bullpen_profile(reliever_rows: pd.DataFrame) -> dict:
    """Build a BF-weighted average bullpen profile from reliever rows."""
    weights = reliever_rows["total_bf"].values

    profile = {"throws": "R"}
    for batter_hand in ("L", "R"):
        rates = {}
        for outcome in OUTCOMES:
            col = f"rate_{outcome}_vs{batter_hand}"
            if col in reliever_rows.columns:
                vals = reliever_rows[col].fillna(LEAGUE_RATES[outcome]).values
                rates[outcome] = np.average(vals, weights=weights)
            else:
                col_overall = f"rate_{outcome}"
                vals = reliever_rows[col_overall].fillna(LEAGUE_RATES[outcome]).values
                rates[outcome] = np.average(vals, weights=weights)

        total = sum(rates.values())
        profile[batter_hand] = {k: v / total for k, v in rates.items()}

    return profile


_DEFAULT_BULLPEN = {
    "throws": "R",
    "L": LEAGUE_RATES.copy(),
    "R": LEAGUE_RATES.copy(),
}


def build_bullpen_profile(reliever_rows: pd.DataFrame = None) -> dict:
    """
    Build a team-level bullpen profile by averaging across relievers.
    If no data, returns league-average rates (a safe default).
    """
    if reliever_rows is None or reliever_rows.empty:
        return _DEFAULT_BULLPEN.copy()
    return _weighted_bullpen_profile(reliever_rows)


def build_tiered_bullpen_profiles(
    reliever_rows: pd.DataFrame = None,
) -> Tuple[dict, dict]:
    """
    Split relievers into high-leverage and low-leverage tiers.
    Returns (high_leverage_profile, low_leverage_profile).

    Tier split: rank by quality metric (K_rate - BB_rate - 3*HR_rate,
    a FIP proxy), split at cumulative 50% of total BF.
    Top half = high-leverage, bottom half = low-leverage.
    If fewer than 4 relievers, both tiers get the same blended profile.
    """
    if reliever_rows is None or reliever_rows.empty:
        default = _DEFAULT_BULLPEN.copy()
        return default, _DEFAULT_BULLPEN.copy()

    if len(reliever_rows) < 4:
        profile = _weighted_bullpen_profile(reliever_rows)
        return profile, profile.copy()

    # Compute quality score for each reliever
    df = reliever_rows.copy()
    k_col = "rate_K" if "rate_K" in df.columns else "rate_K_vsR"
    bb_col = "rate_BB" if "rate_BB" in df.columns else "rate_BB_vsR"
    hr_col = "rate_HR" if "rate_HR" in df.columns else "rate_HR_vsR"

    df["_quality"] = (
        df[k_col].fillna(LEAGUE_RATES["K"])
        - df[bb_col].fillna(LEAGUE_RATES["BB"])
        - 3 * df[hr_col].fillna(LEAGUE_RATES["HR"])
    )

    # Sort by quality descending, split at 50% cumulative BF
    df = df.sort_values("_quality", ascending=False)
    cum_bf = df["total_bf"].cumsum()
    half_bf = df["total_bf"].sum() / 2

    hi_mask = cum_bf <= half_bf
    # Ensure at least 2 in each tier
    if hi_mask.sum() < 2:
        hi_mask.iloc[:2] = True
    if (~hi_mask).sum() < 2:
        hi_mask.iloc[-2:] = False

    hi_rows = df[hi_mask]
    lo_rows = df[~hi_mask]

    hi_profile = _weighted_bullpen_profile(hi_rows)
    lo_profile = _weighted_bullpen_profile(lo_rows)

    return hi_profile, lo_profile
