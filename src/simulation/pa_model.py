"""
Plate appearance probability model.

Combines batter and pitcher outcome profiles using the log5 / odds-ratio
method (Bill James) and applies park factor adjustments to produce a full
PA outcome distribution for each batter-pitcher matchup.
"""

import numpy as np
from typing import Dict, Optional

from src.simulation.constants import LEAGUE_RATES

OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]


def odds_ratio_blend(batter_rate: float, pitcher_rate: float, league_rate: float) -> float:
    """
    Multiplicative odds-ratio method:

        P = batter_rate * pitcher_rate / league_rate

    This is the numerator of the full log5 formula. For rates far from 0.5
    (HR, BB, K, etc.) it produces nearly identical results to full log5.
    For rates near 0.5 (OUT ~0.456), full log5 introduces ~7% compression
    via its denominator, which compounds across 30+ PA per team into a
    meaningful win-probability compression.  The multiplicative form avoids
    this while preserving the correct behavior when both sides are average.
    """
    eps = 1e-9
    b = np.clip(batter_rate, eps, 1.0)
    p = np.clip(pitcher_rate, eps, 1.0)
    l = np.clip(league_rate, eps, 1.0)

    return np.clip((b * p) / l, eps, 1.0)


def compute_pa_probabilities(
    batter_profile: Dict[str, float],
    pitcher_profile: Dict[str, float],
    league_rates: Dict[str, float] = None,
    park_factors: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """
    Produce a normalised PA outcome distribution for one matchup.

    Parameters
    ----------
    batter_profile  : the batter's rates for the appropriate platoon split
                      (e.g. batter_profile["R"] when facing a RHP)
    pitcher_profile : the pitcher's allowed rates for the appropriate platoon
                      split (e.g. pitcher_profile["L"] when facing a LHB)
    park_factors    : optional dict with multiplicative adjustments for
                      HR, 3B, 2B, 1B
    """
    if league_rates is None:
        league_rates = LEAGUE_RATES

    raw = {}
    for outcome in OUTCOMES:
        b = batter_profile.get(outcome, league_rates[outcome])
        p = pitcher_profile.get(outcome, league_rates[outcome])
        raw[outcome] = odds_ratio_blend(b, p, league_rates[outcome])

    # Park factor adjustments (multiplicative, pre-normalisation)
    if park_factors:
        for outcome in ("HR", "3B", "2B", "1B"):
            if outcome in park_factors:
                raw[outcome] *= park_factors[outcome]

    total = sum(raw.values())
    return {k: v / total for k, v in raw.items()}


def sample_pa_outcome(probs: Dict[str, float], rng: np.random.Generator) -> str:
    """Draw a single PA outcome from the distribution."""
    labels = list(probs.keys())
    return rng.choice(labels, p=[probs[k] for k in labels])
