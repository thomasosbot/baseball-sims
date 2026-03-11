"""
Marcel projection system for preseason player rate projections.

Implements Tom Tango's Marcel the Monkey Forecasting System:
  - 3-year weighted history (5/4/3, most recent heaviest)
  - Regression to league average
  - Age adjustment (+0.6%/yr under 29, -0.3%/yr over 29)

Produces per-player projected PA outcome rates in the same 8-category
format (K, BB, HBP, HR, 3B, 2B, 1B, OUT) used throughout the model.
"""

import numpy as np
import pandas as pd
from collections import defaultdict
from typing import Dict, Optional, Tuple

from src.data.process import OUTCOMES, _classify_outcome
from src.simulation.constants import LEAGUE_RATES
import config

BHQ_BLEND_WEIGHT = getattr(config, "BHQ_BLEND_WEIGHT", 0.0)

# Marcel constants (configurable via config.py)
WEIGHTS = getattr(config, "MARCEL_WEIGHTS", (5, 4, 3))
BATTER_REGRESSION_PA = getattr(config, "MARCEL_BATTER_REGRESSION", 1200)
PITCHER_REGRESSION_BF = getattr(config, "MARCEL_PITCHER_REGRESSION", 450)
AGE_PEAK = getattr(config, "MARCEL_AGE_PEAK", 29)
AGE_YOUNG_RATE = getattr(config, "MARCEL_AGE_YOUNG_RATE", 0.006)
AGE_OLD_RATE = getattr(config, "MARCEL_AGE_OLD_RATE", 0.003)


def _aggregate_season(statcast_df: pd.DataFrame) -> Tuple[dict, dict]:
    """
    Aggregate one season of raw Statcast into per-player outcome counts.

    Returns (batter_stats, pitcher_stats) where each is:
        {player_id: {
            "total": int,
            "counts": {outcome: int},
            "splits": {"vsL": {"total": int, outcome: int}, "vsR": {...}},
            "age": int,
            "throws": str,  # pitchers only
        }}
    """
    pa = statcast_df[statcast_df["events"].notna()].copy()
    pa["outcome"] = pa["events"].apply(_classify_outcome)
    pa = pa[pa["outcome"].notna()]

    batters = {}
    pitchers = {}

    # Batter aggregation
    for batter_id, grp in pa.groupby("batter"):
        batter_id = int(batter_id)
        total = len(grp)
        counts = grp["outcome"].value_counts().to_dict()
        age = int(grp["age_bat"].mode().iloc[0]) if "age_bat" in grp.columns else None

        splits = {}
        for hand in ("L", "R"):
            sub = grp[grp["p_throws"] == hand]
            if len(sub) > 0:
                splits[f"vs{hand}"] = {
                    "total": len(sub),
                    **sub["outcome"].value_counts().to_dict(),
                }
            else:
                splits[f"vs{hand}"] = {"total": 0}

        # Batter handedness
        stands = grp["stand"].dropna().unique()
        if len(stands) > 1:
            bats = "S"
        elif len(stands) == 1:
            bats = stands[0]
        else:
            bats = "R"

        batters[batter_id] = {
            "total": total,
            "counts": {o: counts.get(o, 0) for o in OUTCOMES},
            "splits": splits,
            "age": age,
            "bats": bats,
        }

    # Pitcher aggregation
    for pitcher_id, grp in pa.groupby("pitcher"):
        pitcher_id = int(pitcher_id)
        total = len(grp)
        counts = grp["outcome"].value_counts().to_dict()
        age = int(grp["age_pit"].mode().iloc[0]) if "age_pit" in grp.columns else None
        throws = grp["p_throws"].mode().iloc[0] if "p_throws" in grp.columns else "R"

        splits = {}
        for hand in ("L", "R"):
            sub = grp[grp["stand"] == hand]
            if len(sub) > 0:
                splits[f"vs{hand}"] = {
                    "total": len(sub),
                    **sub["outcome"].value_counts().to_dict(),
                }
            else:
                splits[f"vs{hand}"] = {"total": 0}

        pitchers[pitcher_id] = {
            "total": total,
            "counts": {o: counts.get(o, 0) for o in OUTCOMES},
            "splits": splits,
            "age": age,
            "throws": throws,
        }

    return batters, pitchers


def _age_multiplier(age_in_projection_year: int) -> float:
    """Marcel age adjustment: multiply all rates by this factor, then renormalize."""
    if age_in_projection_year is None:
        return 1.0
    if age_in_projection_year < AGE_PEAK:
        return 1.0 + AGE_YOUNG_RATE * (AGE_PEAK - age_in_projection_year)
    elif age_in_projection_year > AGE_PEAK:
        return 1.0 - AGE_OLD_RATE * (age_in_projection_year - AGE_PEAK)
    return 1.0


def _weighted_rates(
    yearly_stats: list,
    weights: tuple,
    regression_constant: int,
    league_rates: dict,
    age_mult: float,
) -> dict:
    """
    Compute Marcel projected rates from up to 3 years of data.

    yearly_stats: list of (counts_dict, total_pa) tuples, most recent first
    weights: year weights (5, 4, 3)
    regression_constant: PA for regression denominator
    league_rates: league average rates
    age_mult: age adjustment multiplier
    """
    # Accumulate weighted counts
    weighted_counts = {o: 0.0 for o in OUTCOMES}
    weighted_total = 0.0

    for i, (counts, total) in enumerate(yearly_stats):
        if i >= len(weights):
            break
        w = weights[i]
        for o in OUTCOMES:
            weighted_counts[o] += counts.get(o, 0) * w
        weighted_total += total * w

    if weighted_total == 0:
        return league_rates.copy()

    # Weighted rates
    raw_rates = {o: weighted_counts[o] / weighted_total for o in OUTCOMES}

    # Regression to league average
    player_weight = weighted_total / (weighted_total + regression_constant)
    league_weight = 1.0 - player_weight

    regressed = {}
    for o in OUTCOMES:
        rate = raw_rates[o] * player_weight + league_rates[o] * league_weight
        # Age adjustment on contact-quality outcomes (not K/BB/HBP which age differently)
        if o not in ("K", "BB", "HBP"):
            rate *= age_mult
        regressed[o] = max(rate, 0.0)

    # Renormalize to sum to 1
    total = sum(regressed.values())
    if total > 0:
        regressed = {o: v / total for o, v in regressed.items()}

    return regressed


def project_marcel(
    statcast_by_year: Dict[int, pd.DataFrame],
    projection_year: int,
    league_rates: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """
    Generate Marcel projections for a given season.

    Args:
        statcast_by_year: {year: raw_statcast_df} for up to 3 prior years
        projection_year: the year being projected (e.g., 2024)
        league_rates: override league average rates (default: LEAGUE_RATES)

    Returns:
        (batter_projections, pitcher_projections) where each is:
        {player_id: {
            "rates": {outcome: prob},
            "rates_vsL": {outcome: prob},
            "rates_vsR": {outcome: prob},
            "total_pa": float,  # weighted PA (for confidence)
            "age": int,
            "bats": str,  # batters only
            "throws": str,  # pitchers only
        }}
    """
    if league_rates is None:
        league_rates = LEAGUE_RATES

    # Aggregate each available year
    years_needed = [projection_year - 1, projection_year - 2, projection_year - 3]
    yearly_batter_stats = {}  # {year: {player_id: stats}}
    yearly_pitcher_stats = {}

    for yr in years_needed:
        if yr in statcast_by_year:
            b, p = _aggregate_season(statcast_by_year[yr])
            yearly_batter_stats[yr] = b
            yearly_pitcher_stats[yr] = p

    if not yearly_batter_stats:
        return {}, {}

    # --- Project batters ---
    batter_projections = {}
    all_batter_ids = set()
    for yr_stats in yearly_batter_stats.values():
        all_batter_ids.update(yr_stats.keys())

    for pid in all_batter_ids:
        # Collect yearly data, most recent first
        yearly_data = []
        yearly_split_data = {"vsL": [], "vsR": []}
        most_recent_age = None
        most_recent_year = None
        bats = "R"

        for yr in years_needed:
            if yr not in yearly_batter_stats:
                continue
            stats = yearly_batter_stats[yr].get(pid)
            if stats is None:
                continue
            yearly_data.append((stats["counts"], stats["total"]))
            for hand in ("vsL", "vsR"):
                split = stats["splits"].get(hand, {"total": 0})
                split_total = split.get("total", 0)
                split_counts = {o: split.get(o, 0) for o in OUTCOMES}
                yearly_split_data[hand].append((split_counts, split_total))
            if most_recent_age is None and stats["age"] is not None:
                most_recent_age = stats["age"]
                most_recent_year = yr
            bats = stats.get("bats", bats)

        if not yearly_data:
            continue

        # Age in projection year
        if most_recent_age is not None and most_recent_year is not None:
            proj_age = most_recent_age + (projection_year - most_recent_year)
        else:
            proj_age = None
        age_mult = _age_multiplier(proj_age)

        # Weighted total PA (for confidence tracking)
        weighted_pa = sum(
            total * WEIGHTS[i] for i, (_, total) in enumerate(yearly_data) if i < len(WEIGHTS)
        ) / sum(WEIGHTS[:len(yearly_data)])

        # Overall rates
        rates = _weighted_rates(yearly_data, WEIGHTS, BATTER_REGRESSION_PA, league_rates, age_mult)

        # Split rates
        split_rates = {}
        for hand in ("vsL", "vsR"):
            if yearly_split_data[hand] and any(t > 0 for _, t in yearly_split_data[hand]):
                split_rates[hand] = _weighted_rates(
                    yearly_split_data[hand], WEIGHTS, BATTER_REGRESSION_PA, league_rates, age_mult
                )
            else:
                split_rates[hand] = rates.copy()

        batter_projections[pid] = {
            "rates": rates,
            "rates_vsL": split_rates["vsL"],
            "rates_vsR": split_rates["vsR"],
            "total_pa": weighted_pa,
            "age": proj_age,
            "bats": bats,
        }

    # --- Project pitchers ---
    pitcher_projections = {}
    all_pitcher_ids = set()
    for yr_stats in yearly_pitcher_stats.values():
        all_pitcher_ids.update(yr_stats.keys())

    for pid in all_pitcher_ids:
        yearly_data = []
        yearly_split_data = {"vsL": [], "vsR": []}
        most_recent_age = None
        most_recent_year = None
        throws = "R"

        for yr in years_needed:
            if yr not in yearly_pitcher_stats:
                continue
            stats = yearly_pitcher_stats[yr].get(pid)
            if stats is None:
                continue
            yearly_data.append((stats["counts"], stats["total"]))
            for hand in ("vsL", "vsR"):
                split = stats["splits"].get(hand, {"total": 0})
                split_total = split.get("total", 0)
                split_counts = {o: split.get(o, 0) for o in OUTCOMES}
                yearly_split_data[hand].append((split_counts, split_total))
            if most_recent_age is None and stats["age"] is not None:
                most_recent_age = stats["age"]
                most_recent_year = yr
            throws = stats.get("throws", throws)

        if not yearly_data:
            continue

        if most_recent_age is not None and most_recent_year is not None:
            proj_age = most_recent_age + (projection_year - most_recent_year)
        else:
            proj_age = None
        # Invert age adjustment for pitchers (older = worse, but we project rates allowed)
        # A young pitcher improving means lower rates allowed, so we divide instead of multiply
        age_mult_pitcher = 1.0 / _age_multiplier(proj_age) if _age_multiplier(proj_age) != 0 else 1.0

        weighted_bf = sum(
            total * WEIGHTS[i] for i, (_, total) in enumerate(yearly_data) if i < len(WEIGHTS)
        ) / sum(WEIGHTS[:len(yearly_data)])

        rates = _weighted_rates(yearly_data, WEIGHTS, PITCHER_REGRESSION_BF, league_rates, age_mult_pitcher)

        split_rates = {}
        for hand in ("vsL", "vsR"):
            if yearly_split_data[hand] and any(t > 0 for _, t in yearly_split_data[hand]):
                split_rates[hand] = _weighted_rates(
                    yearly_split_data[hand], WEIGHTS, PITCHER_REGRESSION_BF, league_rates, age_mult_pitcher
                )
            else:
                split_rates[hand] = rates.copy()

        pitcher_projections[pid] = {
            "rates": rates,
            "rates_vsL": split_rates["vsL"],
            "rates_vsR": split_rates["vsR"],
            "total_bf": weighted_bf,
            "age": proj_age,
            "throws": throws,
        }

    print(f"  Marcel projections: {len(batter_projections)} batters, "
          f"{len(pitcher_projections)} pitchers")

    return batter_projections, pitcher_projections


def blend_bhq_marcel(
    marcel_batters: dict,
    marcel_pitchers: dict,
    bhq_batter_rates: dict,
    bhq_pitcher_rates: dict,
    blend_weight: Optional[float] = None,
) -> Tuple[dict, dict]:
    """
    Blend BHQ skills-based rates with Marcel projections.

    For each player present in both:
      blended_rate = w * bhq_rate + (1-w) * marcel_rate
    For platoon splits, apply Marcel's split differential on top of BHQ overall rates.

    Players only in Marcel: keep Marcel rates (100%).
    Players only in BHQ: skip (no Marcel metadata like age/handedness).

    Returns (blended_batters, blended_pitchers) in the same format as project_marcel().
    """
    w = blend_weight if blend_weight is not None else BHQ_BLEND_WEIGHT
    if w <= 0:
        return marcel_batters, marcel_pitchers

    def _blend_rates(marcel_rates: dict, bhq_rates: dict) -> dict:
        blended = {}
        for o in OUTCOMES:
            blended[o] = w * bhq_rates.get(o, marcel_rates[o]) + (1 - w) * marcel_rates[o]
        total = sum(blended.values())
        if total > 0:
            blended = {o: v / total for o, v in blended.items()}
        return blended

    def _apply_split_ratio(overall: dict, marcel_overall: dict, marcel_split: dict) -> dict:
        """Apply Marcel's platoon differential on top of blended overall rates."""
        split = {}
        for o in OUTCOMES:
            if marcel_overall[o] > 0.001:
                ratio = marcel_split[o] / marcel_overall[o]
            else:
                ratio = 1.0
            split[o] = overall[o] * ratio
        total = sum(split.values())
        if total > 0:
            split = {o: v / total for o, v in split.items()}
        return split

    # Blend batters
    blended_batters = {}
    bhq_matched = 0
    for pid, marcel_proj in marcel_batters.items():
        if pid in bhq_batter_rates:
            bhq = bhq_batter_rates[pid]["rates"]
            blended_overall = _blend_rates(marcel_proj["rates"], bhq)
            blended_vsL = _apply_split_ratio(
                blended_overall, marcel_proj["rates"], marcel_proj["rates_vsL"]
            )
            blended_vsR = _apply_split_ratio(
                blended_overall, marcel_proj["rates"], marcel_proj["rates_vsR"]
            )
            blended_batters[pid] = {
                **marcel_proj,
                "rates": blended_overall,
                "rates_vsL": blended_vsL,
                "rates_vsR": blended_vsR,
            }
            bhq_matched += 1
        else:
            blended_batters[pid] = marcel_proj

    # Blend pitchers
    blended_pitchers = {}
    bhq_p_matched = 0
    for pid, marcel_proj in marcel_pitchers.items():
        if pid in bhq_pitcher_rates:
            bhq = bhq_pitcher_rates[pid]["rates"]
            blended_overall = _blend_rates(marcel_proj["rates"], bhq)
            blended_vsL = _apply_split_ratio(
                blended_overall, marcel_proj["rates"], marcel_proj["rates_vsL"]
            )
            blended_vsR = _apply_split_ratio(
                blended_overall, marcel_proj["rates"], marcel_proj["rates_vsR"]
            )
            blended_pitchers[pid] = {
                **marcel_proj,
                "rates": blended_overall,
                "rates_vsL": blended_vsL,
                "rates_vsR": blended_vsR,
            }
            bhq_p_matched += 1
        else:
            blended_pitchers[pid] = marcel_proj

    print(f"  BHQ blended: {bhq_matched}/{len(marcel_batters)} batters, "
          f"{bhq_p_matched}/{len(marcel_pitchers)} pitchers (w={w:.0%})")

    return blended_batters, blended_pitchers
