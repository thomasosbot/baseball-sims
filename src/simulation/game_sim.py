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
    GROUND_BALL_OUT_FRAC,
    SAC_FLY_PROB,
    STARTER_BATTER_LIMIT,
    TTO_HIT_BOOST,
)
from src.simulation.pa_model import compute_pa_probabilities, sample_pa_outcome


# ---------------------------------------------------------------------------
# Baserunner logic
# ---------------------------------------------------------------------------

def _advance_runners(bases: list, hit_type: str, rng: np.random.Generator) -> Tuple[list, int]:
    """
    Move baserunners after a hit.  Returns (new_bases, runs_scored).
    bases is a 3-element list: [1B, 2B, 3B] where each element is
    True (runner present) or False.
    """
    runs = 0

    if hit_type == "HR":
        runs = sum(bases) + 1          # everyone on base + batter
        return [False, False, False], runs

    new_bases = [False, False, False]
    adv = BASE_ADVANCEMENT[hit_type]

    # Process runners from 3rd -> 1st so we don't collide
    for base_idx in (2, 1, 0):          # 3B, 2B, 1B (0-indexed)
        if not bases[base_idx]:
            continue
        base_num = base_idx + 1         # 1-indexed
        info = adv.get(base_num)
        if info is None:
            new_bases[base_idx] = True   # runner stays (shouldn't happen)
            continue
        dest = rng.choice(info["advance_to"], p=info["probs"])
        if dest == "score":
            runs += 1
        else:
            new_bases[int(dest) - 1] = True

    # Place the batter
    if hit_type == "1B":
        new_bases[0] = True
    elif hit_type == "2B":
        new_bases[1] = True
    elif hit_type == "3B":
        new_bases[2] = True

    return new_bases, runs


def _handle_walk(bases: list) -> Tuple[list, int]:
    """Walk / HBP: forced advances only."""
    runs = 0
    new = list(bases)

    if new[0]:                           # runner on 1B — force chain
        if new[1]:                       # runner on 2B too
            if new[2]:                   # bases loaded — force a run
                runs = 1
            else:
                new[2] = True
        else:
            new[1] = True
    new[0] = True                        # batter to 1B
    return new, runs


def _handle_out(bases: list, outs: int, rng: np.random.Generator) -> Tuple[list, int, int]:
    """
    Handle a field out.  Returns (new_bases, runs_scored, extra_outs).
    Models sac flies and double plays.
    """
    new = list(bases)
    runs = 0
    extra_outs = 0

    # Sac fly: runner on 3B scores on a fly out with < 2 outs
    if new[2] and outs < 2 and rng.random() < SAC_FLY_PROB:
        runs += 1
        new[2] = False
        return new, runs, 0

    # Double play: runner on 1B, < 2 outs, ground ball
    if new[0] and outs < 2 and rng.random() < GROUND_BALL_OUT_FRAC:
        if rng.random() < DOUBLE_PLAY_PROB:
            new[0] = False
            extra_outs = 1
            # Runner on 2B advances to 3B on the DP
            if new[1]:
                new[2] = True
                new[1] = False
            return new, runs, extra_outs

    return new, runs, 0


# ---------------------------------------------------------------------------
# Half-inning simulation
# ---------------------------------------------------------------------------

def _apply_tto_boost(pa_probs: dict, tto: int) -> dict:
    """
    Apply times-through-order boost to hit probabilities.
    Increases 1B/2B/3B/HR rates for 2nd and 3rd+ time through,
    then re-normalizes so probabilities sum to 1.
    """
    boost = TTO_HIT_BOOST.get(min(tto, 3), 1.0)
    if boost == 1.0:
        return pa_probs

    adjusted = dict(pa_probs)
    for hit in ("1B", "2B", "3B", "HR"):
        adjusted[hit] *= boost

    total = sum(adjusted.values())
    return {k: v / total for k, v in adjusted.items()}


def _simulate_half_inning(
    lineup: List[dict],
    order_pos: int,
    starter_profile: dict,
    bullpen_profile: dict,
    pitcher_bf: int,
    park_factors: dict,
    rng: np.random.Generator,
    ghost_runner: bool = False,
    batter_tto: Optional[Dict[int, int]] = None,
) -> Tuple[int, int, int, Dict[int, int]]:
    """
    Simulate one half-inning.

    Returns (runs_scored, new_order_pos, new_pitcher_bf, batter_tto).
    batter_tto tracks how many times each lineup slot has faced the starter.
    """
    outs = 0
    bases = [False, False, False]
    runs = 0
    if batter_tto is None:
        batter_tto = {}

    # MLB extra-innings ghost runner on 2B
    if ghost_runner:
        bases[1] = True

    while outs < 3:
        lineup_slot = order_pos % 9
        batter = lineup[lineup_slot]
        batter_hand = batter.get("bats", "R")

        # Choose pitcher (starter vs bullpen)
        is_starter = pitcher_bf < STARTER_BATTER_LIMIT
        if is_starter:
            p_prof = starter_profile
        else:
            p_prof = bullpen_profile

        pitcher_hand = p_prof.get("throws", "R")

        # Switch hitters bat from the opposite side
        if batter_hand == "S":
            effective_hand = "L" if pitcher_hand == "R" else "R"
        else:
            effective_hand = batter_hand

        # Look up correct platoon splits
        # Batter profile keyed by pitcher hand: "what are my rates vs a RHP/LHP?"
        # Pitcher profile keyed by batter hand: "what are my rates vs a RHB/LHB?"
        batter_rates  = batter["profile"].get(pitcher_hand, batter["profile"].get("R"))
        pitcher_rates = p_prof.get(effective_hand, p_prof.get("R"))

        pa_probs = compute_pa_probabilities(
            batter_profile=batter_rates,
            pitcher_profile=pitcher_rates,
            park_factors=park_factors,
        )

        # Apply TTO penalty when facing the starter
        if is_starter:
            tto = batter_tto.get(lineup_slot, 0) + 1
            batter_tto[lineup_slot] = tto
            if tto >= 2:
                pa_probs = _apply_tto_boost(pa_probs, tto)

        outcome = sample_pa_outcome(pa_probs, rng)
        pitcher_bf += 1
        order_pos  += 1

        if outcome == "K":
            outs += 1
        elif outcome in ("BB", "HBP"):
            bases, r = _handle_walk(bases)
            runs += r
        elif outcome in ("1B", "2B", "3B", "HR"):
            bases, r = _advance_runners(bases, outcome, rng)
            runs += r
        elif outcome == "OUT":
            bases, r, extra = _handle_out(bases, outs, rng)
            runs += r
            outs += 1 + extra

    return runs, order_pos % 9, pitcher_bf, batter_tto


# ---------------------------------------------------------------------------
# Full game simulation
# ---------------------------------------------------------------------------

def simulate_game(
    home_lineup: List[dict],
    away_lineup: List[dict],
    home_starter: dict,
    away_starter: dict,
    home_bullpen: dict,
    away_bullpen: dict,
    park_factors: dict,
    rng: np.random.Generator,
) -> Tuple[int, int]:
    """
    Simulate a full 9-inning game (with extra innings if tied).

    Lineup entries: {"profile": {pitcher_hand: {outcome: prob}}, "bats": "L"/"R"}
    Pitcher dicts : {"throws": "L"/"R", "L": {outcome: prob}, "R": {outcome: prob}}

    Returns (away_score, home_score).
    """
    away_score = 0
    home_score = 0
    away_order = 0        # batting order cursor
    home_order = 0
    home_pitcher_bf = 0   # batters faced by home team's pitching staff
    away_pitcher_bf = 0   # batters faced by away team's pitching staff

    # Track times-through-order for each lineup slot vs opposing starter
    away_batter_tto = {}  # away batters vs home starter
    home_batter_tto = {}  # home batters vs away starter

    for inning in range(1, 10):
        # ---- Top: away team bats vs home pitching ----
        r, away_order, home_pitcher_bf, away_batter_tto = _simulate_half_inning(
            lineup=away_lineup,
            order_pos=away_order,
            starter_profile=home_starter,
            bullpen_profile=home_bullpen,
            pitcher_bf=home_pitcher_bf,
            park_factors=park_factors,
            rng=rng,
            batter_tto=away_batter_tto,
        )
        away_score += r

        # ---- Bottom: home team bats vs away pitching ----
        # Walk-off check: skip bottom 9 if home already leads
        if inning == 9 and home_score > away_score:
            break

        r, home_order, away_pitcher_bf, home_batter_tto = _simulate_half_inning(
            lineup=home_lineup,
            order_pos=home_order,
            starter_profile=away_starter,
            bullpen_profile=away_bullpen,
            pitcher_bf=away_pitcher_bf,
            park_factors=park_factors,
            rng=rng,
            batter_tto=home_batter_tto,
        )
        home_score += r

        # Walk-off in bottom of 9th+
        if inning >= 9 and home_score > away_score:
            break

    # ---- Extra innings (Manfred runner on 2B) ----
    # Don't pass TTO — starters are already out
    extra = 0
    while away_score == home_score and extra < 10:
        extra += 1

        # Top
        r, away_order, home_pitcher_bf, _ = _simulate_half_inning(
            lineup=away_lineup,
            order_pos=away_order,
            starter_profile=home_bullpen,   # starters are out by extras
            bullpen_profile=home_bullpen,
            pitcher_bf=STARTER_BATTER_LIMIT + 1,  # force bullpen
            park_factors=park_factors,
            rng=rng,
            ghost_runner=True,
        )
        away_score += r

        # Bottom
        r, home_order, away_pitcher_bf, _ = _simulate_half_inning(
            lineup=home_lineup,
            order_pos=home_order,
            starter_profile=away_bullpen,
            bullpen_profile=away_bullpen,
            pitcher_bf=STARTER_BATTER_LIMIT + 1,
            park_factors=park_factors,
            rng=rng,
            ghost_runner=True,
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
) -> Dict:
    """
    Run N simulations and return win probabilities + score distributions.
    """
    rng = np.random.default_rng(seed)

    home_wins = 0
    home_runs_list = []
    away_runs_list = []

    for _ in range(n_simulations):
        a, h = simulate_game(
            home_lineup, away_lineup,
            home_starter, away_starter,
            home_bullpen, away_bullpen,
            park_factors, rng,
        )
        home_runs_list.append(h)
        away_runs_list.append(a)
        if h > a:
            home_wins += 1

    home_wp = home_wins / n_simulations
    total_runs = np.array(home_runs_list) + np.array(away_runs_list)

    return {
        "home_win_prob": home_wp,
        "away_win_prob": 1.0 - home_wp,
        "avg_home_runs": np.mean(home_runs_list),
        "avg_away_runs": np.mean(away_runs_list),
        "avg_total_runs": float(np.mean(total_runs)),
        "std_total_runs": float(np.std(total_runs)),
        "total_runs_dist": total_runs,  # raw distribution for over/under
        "n_simulations": n_simulations,
    }
