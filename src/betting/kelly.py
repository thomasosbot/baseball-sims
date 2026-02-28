"""
Kelly Criterion bet sizing for bankroll management.

Full Kelly is the mathematically optimal growth-rate strategy but assumes
the model's probabilities are perfectly calibrated.  In practice we use
fractional Kelly (default 25%) to reduce variance and account for model error.
"""

import numpy as np


def kelly_fraction(win_prob: float, decimal_odds: float) -> float:
    """
    Full Kelly bet fraction.

        f* = (b*p - q) / b

    where b = decimal_odds - 1, p = win probability, q = 1 - p.
    Returns 0 when the bet has negative or zero expected value.
    """
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    f = (b * win_prob - (1 - win_prob)) / b
    return max(0.0, f)


def size_bet(
    win_prob: float,
    decimal_odds: float,
    bankroll: float,
    fraction: float = 0.25,
    max_pct: float = 0.05,
) -> dict:
    """
    Calculate the dollar amount to wager.

    fraction : what fraction of full Kelly to use (0.25 = quarter Kelly)
    max_pct  : hard cap — never risk more than this fraction of bankroll

    Returns dict with kelly_full, kelly_adj, bet_fraction, bet_dollars.
    """
    full = kelly_fraction(win_prob, decimal_odds)
    adj  = full * fraction
    capped = min(adj, max_pct)

    return {
        "kelly_full":   full,
        "kelly_adj":    adj,
        "bet_fraction": capped,
        "bet_dollars":  round(bankroll * capped, 2),
    }


def expected_log_growth(win_prob: float, decimal_odds: float, bet_frac: float) -> float:
    """
    Expected log-growth of bankroll for a single bet.
    Positive value = the bet grows the bankroll in expectation.
    """
    if bet_frac <= 0:
        return 0.0
    gain = np.log(1 + bet_frac * (decimal_odds - 1))
    loss = np.log(1 - bet_frac)
    return win_prob * gain + (1 - win_prob) * loss
