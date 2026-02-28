"""
Batter feature engineering.
Handles regression to the mean and building per-batter PA outcome profiles
with platoon splits (vs LHP / vs RHP).
"""

import numpy as np
import pandas as pd

import config
from src.simulation.constants import LEAGUE_RATES

# Support both old single-scale and new split-scale configs
_BAT_SCALE = getattr(config, 'BATTER_REGRESSION_SCALE', getattr(config, 'REGRESSION_SCALE', 0.40))

OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]

# How many PA of league-average data to blend with observed data.
# Lower = less regression (outcome stabilises quickly with sample size).
# Higher = more regression (outcome is noisy and needs a larger sample).
REGRESSION_PA = {
    "K":   200,
    "BB":  250,
    "HBP": 500,
    "HR":  400,
    "3B":  800,
    "2B":  500,
    "1B":  400,
    "OUT": 300,
}


def regress_to_mean(
    observed_rate: float,
    sample_size: int,
    league_rate: float,
    regression_weight: int,
) -> float:
    """
    Bayesian-style regression toward the league mean.

    posterior = (observed * n + league * weight) / (n + weight)

    With 0 PA the result equals the league rate.
    With infinite PA the result equals the observed rate.
    """
    return (observed_rate * sample_size + league_rate * regression_weight) / (
        sample_size + regression_weight
    )


def build_batter_profile(batter_row: pd.Series) -> dict:
    """
    Build a complete batter profile with regressed PA outcome rates.

    Returns {"R": {outcome: prob, ...}, "L": {outcome: prob, ...}}
    where "R" means "these are my rates when facing a RHP" and "L" means
    "these are my rates when facing a LHP".

    Input row must contain rate_{outcome} columns and optionally
    rate_{outcome}_vsR / rate_{outcome}_vsL columns from process.py.
    """
    total_pa = batter_row.get("total_pa", 300)

    profile = {}
    for pitcher_hand in ("R", "L"):
        split_pa = batter_row.get(f"pa_vs{pitcher_hand}", total_pa)

        rates = {}
        for outcome in OUTCOMES:
            col_split   = f"rate_{outcome}_vs{pitcher_hand}"
            col_overall = f"rate_{outcome}"

            # Use platoon split if available, fall back to overall
            if col_split in batter_row.index and not np.isnan(batter_row[col_split]):
                raw = batter_row[col_split]
                n   = split_pa
            elif col_overall in batter_row.index and not np.isnan(batter_row[col_overall]):
                raw = batter_row[col_overall]
                n   = total_pa
            else:
                raw = LEAGUE_RATES[outcome]
                n   = 0

            rates[outcome] = regress_to_mean(
                raw, n, LEAGUE_RATES[outcome], int(REGRESSION_PA[outcome] * _BAT_SCALE)
            )

        # Normalise so probabilities sum to 1
        total = sum(rates.values())
        profile[pitcher_hand] = {k: v / total for k, v in rates.items()}

    return profile
