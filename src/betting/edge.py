"""
Edge calculation: compare model win probabilities to market lines.
"""

import pandas as pd
from typing import Dict

from src.betting.odds import american_to_decimal


def calculate_edge(
    model_prob: float,
    market_no_vig_prob: float,
    market_odds: float,
) -> Dict:
    """
    Compute the edge for a potential bet.

    edge       = model_prob - market_no_vig_prob  (positive = model thinks side is undervalued)
    ev_per_unit = model_prob * (decimal - 1) - (1 - model_prob)   (expected profit per $1 wagered)
    """
    decimal = american_to_decimal(market_odds)
    edge    = model_prob - market_no_vig_prob
    ev      = model_prob * (decimal - 1) - (1 - model_prob)

    return {
        "model_prob":      model_prob,
        "market_prob":     market_no_vig_prob,
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
) -> pd.DataFrame:
    """
    For a single game, compare model probabilities to market odds and return
    any sides that exceed min_edge.
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
        )
        e["team"] = home_team if side == "home" else away_team
        e["side"] = side
        e["book"] = g[book_key]
        edges.append(e)

    df = pd.DataFrame(edges)
    return df[df["edge"] >= min_edge]
