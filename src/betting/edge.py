"""
Edge calculation: compare model win probabilities to market lines.

Confidence-gated betting: raw model probabilities are shrunk toward market
probabilities based on a game-level confidence score.  This prevents the
model from over-betting when data is thin or when it wildly disagrees with
the market.
"""

import pandas as pd
from typing import Dict

from src.betting.odds import american_to_decimal


def compute_game_confidence(
    cumulative_pitchers: int = 0,
    model_prob: float = 0.5,
    market_prob: float = 0.5,
) -> float:
    """
    Compute a 0-1 confidence score for a single game prediction.

    Components:
      1. Season depth — more pitchers tracked → more reliable profiles.
         Maps cumulative_pitchers from 850→1300 onto 0.2→1.0 (linear clamp).
      2. Model-market agreement — penalise when model and market disagree on
         the favourite (flipped favourite = low confidence).

    Returns a single float in [0, 1].
    """
    # --- Season depth (0.2 to 1.0) ---
    if cumulative_pitchers <= 850:
        depth = 0.2
    elif cumulative_pitchers >= 1300:
        depth = 1.0
    else:
        depth = 0.2 + 0.8 * (cumulative_pitchers - 850) / (1300 - 850)

    # --- Model-market agreement ---
    # If both agree on the favourite, agreement = 1.0.
    # If they disagree (one says >0.5, other says <0.5), penalise.
    model_fav_home = model_prob >= 0.5
    market_fav_home = market_prob >= 0.5
    if model_fav_home == market_fav_home:
        agreement = 1.0
    else:
        # Penalise proportional to how far apart they are
        disagreement = abs(model_prob - market_prob)
        agreement = max(0.3, 1.0 - disagreement * 2)

    return depth * agreement


def calculate_edge(
    model_prob: float,
    market_no_vig_prob: float,
    market_odds: float,
    confidence: float = 1.0,
    alpha: float = 1.0,
) -> Dict:
    """
    Compute the edge for a potential bet.

    The model probability is shrunk toward the market using both alpha
    (how much to trust the model vs market) and confidence (game-level
    data quality):
        adjusted_prob = market + (alpha * confidence) * (model - market)

    alpha=1.0 preserves backward compatibility (pure confidence gating).

    edge       = adjusted_prob - market_no_vig_prob
    ev_per_unit = adjusted_prob * (decimal - 1) - (1 - adjusted_prob)
    """
    # Shrink model prob toward market based on alpha and confidence
    adjusted_prob = market_no_vig_prob + (alpha * confidence) * (model_prob - market_no_vig_prob)

    decimal = american_to_decimal(market_odds)
    edge    = adjusted_prob - market_no_vig_prob
    ev      = adjusted_prob * (decimal - 1) - (1 - adjusted_prob)

    return {
        "model_prob":      model_prob,
        "adjusted_prob":   adjusted_prob,
        "market_prob":     market_no_vig_prob,
        "confidence":      confidence,
        "edge":            edge,
        "ev_per_unit":     ev,
        "roi_pct":         ev * 100,
        "decimal_odds":    decimal,
        "american_odds":   market_odds,
        "bet_recommended": edge > 0.03 and ev > 0,
    }


def find_edges(
    sim_results: Dict,
    odds_df: pd.DataFrame,
    home_team: str,
    away_team: str,
    min_edge: float = 0.03,
    max_edge: float = 0.15,
    alpha: float = 1.0,
    confidence: float = 1.0,
) -> pd.DataFrame:
    """
    For a single game, compare model probabilities to market odds and return
    any sides with edge in [min_edge, max_edge].

    Uses alpha + confidence shrinkage to match backtest logic.
    """
    # Match game in odds data (fuzzy on team name substrings)
    mask = (
        odds_df["home_team"].str.contains(home_team, case=False, na=False)
        | odds_df["away_team"].str.contains(away_team, case=False, na=False)
    )
    game = odds_df[mask]
    if game.empty:
        return pd.DataFrame()

    g = game.iloc[0]
    edges = []

    for side, prob_key, odds_key, nv_key, book_key in [
        ("home", "home_win_prob", "best_home_odds", "home_no_vig_prob", "best_home_book"),
        ("away", "away_win_prob", "best_away_odds", "away_no_vig_prob", "best_away_book"),
    ]:
        e = calculate_edge(
            model_prob=sim_results[prob_key],
            market_no_vig_prob=g[nv_key],
            market_odds=g[odds_key],
            confidence=confidence,
            alpha=alpha,
        )
        e["team"] = home_team if side == "home" else away_team
        e["side"] = side
        e["book"] = g[book_key]
        edges.append(e)

    df = pd.DataFrame(edges)
    return df[(df["edge"] >= min_edge) & (df["edge"] <= max_edge)]
