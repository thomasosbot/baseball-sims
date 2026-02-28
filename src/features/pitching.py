"""
Pitcher feature engineering.
Builds per-pitcher PA outcome profiles (rates allowed) with platoon splits.
"""

import numpy as np
import pandas as pd

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


def build_bullpen_profile(reliever_rows: pd.DataFrame = None) -> dict:
    """
    Build a team-level bullpen profile by averaging across relievers.
    If no data, returns league-average rates (a safe default).
    """
    if reliever_rows is None or reliever_rows.empty:
        return {
            "throws": "R",
            "L": LEAGUE_RATES.copy(),
            "R": LEAGUE_RATES.copy(),
        }

    # Weight each reliever by batters faced
    weights = reliever_rows["total_bf"].values
    total_w = weights.sum()

    profile = {"throws": "R"}  # bullpen has mixed handedness; "R" is a placeholder
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
