"""
Backtesting performance metrics.

Key metrics:
- Brier score     : probability calibration (lower = better, 0.25 = coin flip)
- Log loss        : penalises confident wrong predictions
- ROI             : total profit / total staked
- CLV             : closing line value — the gold standard for sharp bettors
- Calibration     : bucketed predicted-vs-actual comparison
"""

import numpy as np
import pandas as pd
from typing import Dict, List


def brier_score(predictions: List[float], outcomes: List[int]) -> float:
    """Mean squared error of probability forecasts.  Lower is better."""
    p = np.array(predictions)
    y = np.array(outcomes)
    return float(np.mean((p - y) ** 2))


def log_loss(predictions: List[float], outcomes: List[int]) -> float:
    """Cross-entropy loss.  Lower is better."""
    p = np.clip(predictions, 1e-9, 1 - 1e-9)
    y = np.array(outcomes)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def roi(bets: pd.DataFrame) -> float:
    """ROI = total profit / total staked.  Expects 'stake' and 'profit' columns."""
    staked = bets["stake"].sum()
    return bets["profit"].sum() / staked if staked else 0.0


def closing_line_value(bets: pd.DataFrame) -> float:
    """
    Average CLV across all bets.
    Positive CLV means you consistently got better odds than the closing line,
    which is the single best predictor of long-term profitability.

    Expects columns: 'odds_taken' and 'closing_odds' (both American).
    """
    from src.betting.odds import american_to_prob
    taken   = bets["odds_taken"].apply(american_to_prob)
    closing = bets["closing_odds"].apply(american_to_prob)
    # CLV = closing implied - taken implied  (lower implied = better price for bettor)
    return float((closing - taken).mean())


def bankroll_growth(bets: pd.DataFrame) -> float:
    """
    Multiplicative bankroll growth from a sequence of bets.
    Expects: 'bet_fraction', 'decimal_odds', 'won' (bool).
    Returns final bankroll as a multiple of starting bankroll (1.0 = break even).
    """
    bank = 1.0
    for _, b in bets.iterrows():
        if b["won"]:
            bank *= 1 + b["bet_fraction"] * (b["decimal_odds"] - 1)
        else:
            bank *= 1 - b["bet_fraction"]
    return bank


def calibration_table(
    predictions: List[float], outcomes: List[int], n_buckets: int = 10
) -> pd.DataFrame:
    """
    Bucket predictions into deciles and compare average predicted probability
    to actual win rate.  A well-calibrated model has these roughly equal.
    """
    p = np.array(predictions)
    y = np.array(outcomes)
    bins = np.linspace(0, 1, n_buckets + 1)
    idx  = np.clip(np.digitize(p, bins) - 1, 0, n_buckets - 1)

    rows = []
    for i in range(n_buckets):
        mask = idx == i
        if not mask.any():
            continue
        rows.append({
            "bucket":        f"{bins[i]:.0%}-{bins[i+1]:.0%}",
            "n":             int(mask.sum()),
            "avg_predicted":  float(p[mask].mean()),
            "actual_win_pct": float(y[mask].mean()),
        })
    return pd.DataFrame(rows)


def summarize_backtest(bets: pd.DataFrame) -> Dict:
    """One-stop summary dict for a backtest run."""
    s = {
        "total_bets":   len(bets),
        "total_staked":  bets["stake"].sum() if "stake" in bets else None,
        "total_profit":  bets["profit"].sum() if "profit" in bets else None,
        "roi_pct":       roi(bets) * 100 if {"stake", "profit"} <= set(bets.columns) else None,
        "win_rate":      bets["won"].mean() if "won" in bets else None,
    }
    if {"model_prob", "won"} <= set(bets.columns):
        s["brier_score"] = brier_score(
            bets["model_prob"].tolist(), bets["won"].astype(int).tolist()
        )
        s["log_loss"] = log_loss(
            bets["model_prob"].tolist(), bets["won"].astype(int).tolist()
        )
    if {"odds_taken", "closing_odds"} <= set(bets.columns):
        s["avg_clv"] = closing_line_value(bets)
    return s
