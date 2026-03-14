"""
Monte Carlo game simulation engine.

Simulates individual baseball games plate-appearance by plate-appearance,
tracking baserunners, outs, innings, and score.  Run thousands of simulations
per matchup to produce a win probability distribution.
"""

import numpy as np
from typing import Dict, List, Optional, Tuple

from src.simulation.constants import (
    BASE_ADVANCEMENT,
    DOUBLE_PLAY_PROB,
    ERROR_RATE,
    GROUND_BALL_OUT_FRAC,
    LEAGUE_AVG_SPEED,
    PRODUCTIVE_OUT_1B_TO_2B,
    PRODUCTIVE_OUT_2B_TO_3B,
    SAC_FLY_PROB,
    SB_ATTEMPT_RATE_1B,
    SB_ATTEMPT_RATE_2B,
    SB_SPEED_FACTOR,
    SB_SUCCESS_RATE,
    STARTER_BATTER_LIMIT,
    TTO_HIT_BOOST,
    WILD_PITCH_RATE,
)
from src.simulation.pa_model import compute_pa_probabilities

OUTCOMES = ["K", "BB", "HBP", "HR", "3B", "2B", "1B", "OUT"]
# Outcome indices for fast lookup
_K, _BB, _HBP, _HR, _3B, _2B, _1B, _OUT = range(8)


# ---------------------------------------------------------------------------
# Pre-compute PA probability arrays for all matchups in a game
# ---------------------------------------------------------------------------

def _compute_bullpen_probs(
    lineup: List[dict],
    bullpen_profile: dict,
    park_factors: dict,
) -> np.ndarray:
    """Compute PA probabilities for a lineup vs a bullpen profile. Returns shape (9, 8)."""
    probs = np.empty((9, 8))
    pitcher_hand = bullpen_profile.get("throws", "R")
    for slot in range(9):
        batter = lineup[slot]
        batter_hand = batter.get("bats", "R")
        if batter_hand == "S":
            eff_hand = "L" if pitcher_hand == "R" else "R"
        else:
            eff_hand = batter_hand
        batter_rates = batter["profile"].get(pitcher_hand, batter["profile"].get("R"))
        pitcher_rates = bullpen_profile.get(eff_hand, bullpen_profile.get("R"))
        bp_probs = compute_pa_probabilities(batter_rates, pitcher_rates, park_factors=park_factors)
        probs[slot] = np.array([bp_probs[o] for o in OUTCOMES])
    return probs


def _precompute_pa_arrays(
    lineup: List[dict],
    starter_profile: dict,
    bullpen_hi_profile: dict,
    bullpen_lo_profile: dict,
    park_factors: dict,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Pre-compute PA probability arrays for all batter-pitcher matchups.

    Returns:
        starter_probs:    shape (9, 3, 8) — [lineup_slot, tto_level_idx, outcome]
        bullpen_hi_probs: shape (9, 8) — high-leverage bullpen
        bullpen_lo_probs: shape (9, 8) — low-leverage bullpen
    """
    starter_probs = np.empty((9, 3, 8))

    pitcher_hand_s = starter_profile.get("throws", "R")
    for slot in range(9):
        batter = lineup[slot]
        batter_hand = batter.get("bats", "R")
        if batter_hand == "S":
            eff_hand_s = "L" if pitcher_hand_s == "R" else "R"
        else:
            eff_hand_s = batter_hand

        batter_rates_s = batter["profile"].get(pitcher_hand_s, batter["profile"].get("R"))
        pitcher_rates_s = starter_profile.get(eff_hand_s, starter_profile.get("R"))

        # Base PA probs vs starter (TTO1 = no boost)
        base_probs = compute_pa_probabilities(batter_rates_s, pitcher_rates_s, park_factors=park_factors)
        arr = np.array([base_probs[o] for o in OUTCOMES])
        starter_probs[slot, 0] = arr

        # TTO2 and TTO3+ boosted versions
        for tto_idx, tto_level in enumerate([2, 3], start=1):
            boost = TTO_HIT_BOOST.get(min(tto_level, 3), 1.0)
            boosted = arr.copy()
            boosted[_HR] *= boost
            boosted[_3B] *= boost
            boosted[_2B] *= boost
            boosted[_1B] *= boost
            boosted /= boosted.sum()
            starter_probs[slot, tto_idx] = boosted

    bullpen_hi_probs = _compute_bullpen_probs(lineup, bullpen_hi_profile, park_factors)
    bullpen_lo_probs = _compute_bullpen_probs(lineup, bullpen_lo_profile, park_factors)

    return starter_probs, bullpen_hi_probs, bullpen_lo_probs


# ---------------------------------------------------------------------------
# Baserunner logic
# ---------------------------------------------------------------------------

def _advance_runners(bases: list, hit_type: str, rng: np.random.Generator) -> Tuple[list, int]:
    runs = 0

    if hit_type == "HR":
        runs = sum(bases) + 1
        return [False, False, False], runs

    new_bases = [False, False, False]
    adv = BASE_ADVANCEMENT[hit_type]

    for base_idx in (2, 1, 0):
        if not bases[base_idx]:
            continue
        base_num = base_idx + 1
        info = adv.get(base_num)
        if info is None:
            new_bases[base_idx] = True
            continue
        dest = rng.choice(info["advance_to"], p=info["probs"])
        if dest == "score":
            runs += 1
        else:
            new_bases[int(dest) - 1] = True

    if hit_type == "1B":
        new_bases[0] = True
    elif hit_type == "2B":
        new_bases[1] = True
    elif hit_type == "3B":
        new_bases[2] = True

    return new_bases, runs


def _handle_walk(bases: list) -> Tuple[list, int]:
    runs = 0
    new = list(bases)
    if new[0]:
        if new[1]:
            if new[2]:
                runs = 1
            else:
                new[2] = True
        else:
            new[1] = True
    new[0] = True
    return new, runs


def _handle_out(bases: list, outs: int, rng: np.random.Generator) -> Tuple[list, int, int]:
    """Handle an out outcome, including sac flies, DPs, errors, and productive outs."""
    new = list(bases)
    runs = 0
    extra_outs = 0

    # --- Reached on error: batter reaches 1B, runners advance one base ---
    # This converts an out into a baserunner (no out recorded)
    if rng.random() < ERROR_RATE:
        if new[2]:
            runs += 1
            new[2] = False
        if new[1]:
            new[2] = True
            new[1] = False
        if new[0]:
            new[1] = True
        new[0] = True
        return new, runs, -1  # -1 signals "no out recorded"

    # --- Sac fly: runner on 3B scores ---
    if new[2] and outs < 2 and rng.random() < SAC_FLY_PROB:
        runs += 1
        new[2] = False
        return new, runs, 0

    # --- Double play ---
    if new[0] and outs < 2 and rng.random() < GROUND_BALL_OUT_FRAC:
        if rng.random() < DOUBLE_PLAY_PROB:
            new[0] = False
            extra_outs = 1
            if new[1]:
                new[2] = True
                new[1] = False
            return new, runs, extra_outs

    # --- Productive out: runners advance on groundball outs ---
    if new[1] and not new[2] and rng.random() < PRODUCTIVE_OUT_2B_TO_3B:
        new[2] = True
        new[1] = False
    if new[0] and not new[1] and rng.random() < PRODUCTIVE_OUT_1B_TO_2B:
        new[1] = True
        new[0] = False

    return new, runs, 0


# ---------------------------------------------------------------------------
# Stolen bases and wild pitches
# ---------------------------------------------------------------------------

def _check_stolen_base(
    bases: list, outs: int, team_speed: float, rng: np.random.Generator,
) -> Tuple[list, int, int]:
    """
    Check for stolen base attempts before a PA.
    Returns (new_bases, runs_scored, outs_added).
    """
    new = list(bases)
    runs = 0
    extra_outs = 0
    speed_ratio = team_speed / LEAGUE_AVG_SPEED

    # Steal of 2B: runner on 1B, 2B empty
    if new[0] and not new[1] and outs < 2:
        attempt_rate = SB_ATTEMPT_RATE_1B * speed_ratio
        if rng.random() < attempt_rate:
            success_rate = SB_SUCCESS_RATE + SB_SPEED_FACTOR * (team_speed - LEAGUE_AVG_SPEED)
            success_rate = np.clip(success_rate, 0.50, 0.95)
            if rng.random() < success_rate:
                new[1] = True
                new[0] = False
            else:
                new[0] = False
                extra_outs = 1

    # Steal of 3B: runner on 2B, 3B empty (rarer)
    elif new[1] and not new[2] and outs < 2:
        attempt_rate = SB_ATTEMPT_RATE_2B * speed_ratio
        if rng.random() < attempt_rate:
            success_rate = SB_SUCCESS_RATE + SB_SPEED_FACTOR * (team_speed - LEAGUE_AVG_SPEED)
            success_rate = np.clip(success_rate, 0.50, 0.95)
            if rng.random() < success_rate:
                new[2] = True
                new[1] = False
            else:
                new[1] = False
                extra_outs = 1

    return new, runs, extra_outs


def _check_wild_pitch(
    bases: list, rng: np.random.Generator,
) -> Tuple[list, int]:
    """
    Check for wild pitch / passed ball before a PA.
    Advances all runners one base; runner on 3B scores.
    """
    if not any(bases):
        return bases, 0
    if rng.random() >= WILD_PITCH_RATE:
        return bases, 0

    new = [False, False, False]
    runs = 0
    if bases[2]:
        runs += 1
    if bases[1]:
        new[2] = True
    if bases[0]:
        new[1] = True
    return new, runs


# ---------------------------------------------------------------------------
# Half-inning simulation (uses pre-computed arrays)
# ---------------------------------------------------------------------------

def _simulate_half_inning(
    starter_probs: np.ndarray,
    bullpen_hi_probs: np.ndarray,
    bullpen_lo_probs: np.ndarray,
    order_pos: int,
    pitcher_bf: int,
    rng: np.random.Generator,
    run_diff: int = 0,
    high_lev_threshold: int = 2,
    ghost_runner: bool = False,
    batter_tto: Optional[Dict[int, int]] = None,
    team_speed: float = 100.0,
) -> Tuple[int, int, int, Dict[int, int]]:
    outs = 0
    bases = [False, False, False]
    runs = 0
    if batter_tto is None:
        batter_tto = {}

    if ghost_runner:
        bases[1] = True

    # Select bullpen tier based on score differential at start of half-inning
    use_hi = abs(run_diff) <= high_lev_threshold
    bullpen_probs = bullpen_hi_probs if use_hi else bullpen_lo_probs

    while outs < 3:
        # --- Stolen base check (before PA) ---
        if any(bases):
            bases, sb_runs, sb_outs = _check_stolen_base(bases, outs, team_speed, rng)
            runs += sb_runs
            outs += sb_outs
            if outs >= 3:
                break

            # --- Wild pitch check (before PA) ---
            bases, wp_runs = _check_wild_pitch(bases, rng)
            runs += wp_runs

        lineup_slot = order_pos % 9
        is_starter = pitcher_bf < STARTER_BATTER_LIMIT

        if is_starter:
            tto = batter_tto.get(lineup_slot, 0) + 1
            batter_tto[lineup_slot] = tto
            tto_idx = min(tto, 3) - 1  # 0, 1, 2
            probs = starter_probs[lineup_slot, tto_idx]
        else:
            probs = bullpen_probs[lineup_slot]

        outcome_idx = rng.choice(8, p=probs)
        pitcher_bf += 1
        order_pos += 1

        if outcome_idx == _K:
            outs += 1
        elif outcome_idx == _BB or outcome_idx == _HBP:
            bases, r = _handle_walk(bases)
            runs += r
        elif outcome_idx == _HR:
            bases, r = _advance_runners(bases, "HR", rng)
            runs += r
        elif outcome_idx == _3B:
            bases, r = _advance_runners(bases, "3B", rng)
            runs += r
        elif outcome_idx == _2B:
            bases, r = _advance_runners(bases, "2B", rng)
            runs += r
        elif outcome_idx == _1B:
            bases, r = _advance_runners(bases, "1B", rng)
            runs += r
        elif outcome_idx == _OUT:
            bases, r, extra = _handle_out(bases, outs, rng)
            runs += r
            if extra == -1:
                # Reached on error — no out recorded
                pass
            else:
                outs += 1 + extra

    return runs, order_pos % 9, pitcher_bf, batter_tto


# ---------------------------------------------------------------------------
# Full game simulation
# ---------------------------------------------------------------------------

def simulate_game(
    home_starter_probs: np.ndarray,
    home_bullpen_hi_probs: np.ndarray,
    home_bullpen_lo_probs: np.ndarray,
    away_starter_probs: np.ndarray,
    away_bullpen_hi_probs: np.ndarray,
    away_bullpen_lo_probs: np.ndarray,
    rng: np.random.Generator,
    home_speed: float = 100.0,
    away_speed: float = 100.0,
    high_lev_threshold: int = 2,
) -> Tuple[int, int]:
    """
    Simulate a full 9-inning game using pre-computed PA probability arrays.
    Uses tiered bullpen: high-leverage arms in close games, low-leverage in blowouts.
    Returns (away_score, home_score).
    """
    away_score = 0
    home_score = 0
    away_order = 0
    home_order = 0
    home_pitcher_bf = 0
    away_pitcher_bf = 0
    away_batter_tto = {}
    home_batter_tto = {}

    for inning in range(1, 10):
        # Top: away bats vs home pitching
        r, away_order, home_pitcher_bf, away_batter_tto = _simulate_half_inning(
            home_starter_probs, home_bullpen_hi_probs, home_bullpen_lo_probs,
            away_order, home_pitcher_bf, rng,
            run_diff=home_score - away_score,
            high_lev_threshold=high_lev_threshold,
            batter_tto=away_batter_tto,
            team_speed=away_speed,
        )
        away_score += r

        # Bottom: home bats vs away pitching
        if inning == 9 and home_score > away_score:
            break

        r, home_order, away_pitcher_bf, home_batter_tto = _simulate_half_inning(
            away_starter_probs, away_bullpen_hi_probs, away_bullpen_lo_probs,
            home_order, away_pitcher_bf, rng,
            run_diff=away_score - home_score,
            high_lev_threshold=high_lev_threshold,
            batter_tto=home_batter_tto,
            team_speed=home_speed,
        )
        home_score += r

        if inning >= 9 and home_score > away_score:
            break

    # Extra innings — always high-leverage (close game by definition)
    extra = 0
    while away_score == home_score and extra < 10:
        extra += 1

        hi_as_starter = home_bullpen_hi_probs.reshape(9, 1, 8).repeat(3, axis=1)
        r, away_order, home_pitcher_bf, _ = _simulate_half_inning(
            hi_as_starter, home_bullpen_hi_probs, home_bullpen_lo_probs,
            away_order, STARTER_BATTER_LIMIT + 1, rng,
            run_diff=0,
            high_lev_threshold=high_lev_threshold,
            ghost_runner=True,
            team_speed=away_speed,
        )
        away_score += r

        hi_as_starter = away_bullpen_hi_probs.reshape(9, 1, 8).repeat(3, axis=1)
        r, home_order, away_pitcher_bf, _ = _simulate_half_inning(
            hi_as_starter, away_bullpen_hi_probs, away_bullpen_lo_probs,
            home_order, STARTER_BATTER_LIMIT + 1, rng,
            run_diff=away_score - home_score,
            high_lev_threshold=high_lev_threshold,
            ghost_runner=True,
            team_speed=home_speed,
        )
        home_score += r

        if home_score > away_score:
            break

    return away_score, home_score


def monte_carlo_win_probability(
    home_lineup: List[dict],
    away_lineup: List[dict],
    home_starter: dict,
    away_starter: dict,
    home_bullpen: dict,
    away_bullpen: dict,
    park_factors: dict,
    n_simulations: int = 10_000,
    seed: Optional[int] = None,
    home_bullpen_lo: dict = None,
    away_bullpen_lo: dict = None,
) -> Dict:
    """
    Run N simulations and return win probabilities + score distributions.

    Pre-computes all PA probability arrays once, then runs the fast sim loop.

    Supports tiered bullpens: home_bullpen / away_bullpen are treated as the
    high-leverage tier. If home_bullpen_lo / away_bullpen_lo are provided,
    they are used as the low-leverage tier; otherwise the high-leverage
    profile is used for both (backward compatible).
    """
    from config import BULLPEN_HIGH_LEV_THRESHOLD
    high_lev_threshold = BULLPEN_HIGH_LEV_THRESHOLD

    if home_bullpen_lo is None:
        home_bullpen_lo = home_bullpen
    if away_bullpen_lo is None:
        away_bullpen_lo = away_bullpen

    rng = np.random.default_rng(seed)

    # Pre-compute all matchup probabilities (the expensive part — done once)
    # "home_*" = probs for away batters vs home starter/bullpen
    # "away_*" = probs for home batters vs away starter/bullpen
    home_s_probs, home_bhi_probs, home_blo_probs = _precompute_pa_arrays(
        away_lineup, home_starter, home_bullpen, home_bullpen_lo, park_factors
    )
    away_s_probs, away_bhi_probs, away_blo_probs = _precompute_pa_arrays(
        home_lineup, away_starter, away_bullpen, away_bullpen_lo, park_factors
    )

    # Team average speed for stolen base model (BHQ SPD, default 100)
    home_speed = float(np.mean([b.get("speed", LEAGUE_AVG_SPEED) for b in home_lineup]))
    away_speed = float(np.mean([b.get("speed", LEAGUE_AVG_SPEED) for b in away_lineup]))

    home_wins = 0
    home_runs_list = np.empty(n_simulations, dtype=np.int32)
    away_runs_list = np.empty(n_simulations, dtype=np.int32)

    for i in range(n_simulations):
        a, h = simulate_game(
            home_s_probs, home_bhi_probs, home_blo_probs,
            away_s_probs, away_bhi_probs, away_blo_probs,
            rng,
            home_speed=home_speed,
            away_speed=away_speed,
            high_lev_threshold=high_lev_threshold,
        )
        home_runs_list[i] = h
        away_runs_list[i] = a
        if h > a:
            home_wins += 1

    home_wp = home_wins / n_simulations
    total_runs = home_runs_list + away_runs_list

    return {
        "home_win_prob": home_wp,
        "away_win_prob": 1.0 - home_wp,
        "avg_home_runs": float(np.mean(home_runs_list)),
        "avg_away_runs": float(np.mean(away_runs_list)),
        "avg_total_runs": float(np.mean(total_runs)),
        "std_total_runs": float(np.std(total_runs)),
        "total_runs_dist": total_runs,
        "n_simulations": n_simulations,
    }
