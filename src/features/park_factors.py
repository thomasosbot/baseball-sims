"""
Park factor data and application.
Source: FanGraphs 5-year regressed park factors (2024 baseline).
Values are multiplicative vs. league average (1.0 = perfectly neutral park).
"""

# 5-year regressed park factors by team abbreviation.
# HR factors show the most variance. Coors Field is a massive outlier.
PARK_FACTORS_2024 = {
    #          HR      1B     2B     3B
    "COL": {"HR": 1.22, "1B": 1.08, "2B": 1.07, "3B": 1.40},  # Coors Field
    "CIN": {"HR": 1.12, "1B": 1.03, "2B": 1.05, "3B": 0.98},
    "TEX": {"HR": 1.10, "1B": 1.02, "2B": 1.04, "3B": 1.00},
    "PHI": {"HR": 1.09, "1B": 1.01, "2B": 1.03, "3B": 0.90},
    "BAL": {"HR": 1.08, "1B": 1.01, "2B": 1.02, "3B": 0.85},
    "BOS": {"HR": 1.07, "1B": 1.04, "2B": 1.10, "3B": 0.80},  # Fenway wall -> lots of doubles
    "HOU": {"HR": 1.06, "1B": 1.00, "2B": 1.01, "3B": 0.90},
    "ATL": {"HR": 1.05, "1B": 1.01, "2B": 1.02, "3B": 0.95},
    "ARI": {"HR": 1.04, "1B": 1.01, "2B": 1.02, "3B": 0.95},
    "LAA": {"HR": 1.03, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "NYY": {"HR": 1.03, "1B": 0.99, "2B": 0.99, "3B": 0.80},  # short right-field porch
    "CWS": {"HR": 1.03, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "TOR": {"HR": 1.02, "1B": 1.00, "2B": 1.00, "3B": 0.90},
    "STL": {"HR": 1.00, "1B": 1.00, "2B": 1.01, "3B": 0.95},
    "MIL": {"HR": 1.00, "1B": 1.00, "2B": 1.00, "3B": 1.00},
    "DET": {"HR": 0.99, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "WSN": {"HR": 0.99, "1B": 1.00, "2B": 1.00, "3B": 1.00},
    "CLE": {"HR": 0.98, "1B": 1.00, "2B": 1.01, "3B": 1.00},
    "MIN": {"HR": 0.97, "1B": 1.00, "2B": 1.00, "3B": 0.90},
    "CHC": {"HR": 0.97, "1B": 1.00, "2B": 1.01, "3B": 1.00},
    "PIT": {"HR": 0.97, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "KCR": {"HR": 0.96, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "TBR": {"HR": 0.96, "1B": 1.00, "2B": 1.00, "3B": 0.90},
    "SFG": {"HR": 0.95, "1B": 1.00, "2B": 1.00, "3B": 1.00},
    "LAD": {"HR": 0.95, "1B": 1.00, "2B": 1.00, "3B": 0.95},
    "NYM": {"HR": 0.95, "1B": 1.00, "2B": 1.00, "3B": 0.90},
    "SDP": {"HR": 0.94, "1B": 1.00, "2B": 0.99, "3B": 0.95},
    "SEA": {"HR": 0.94, "1B": 0.99, "2B": 0.99, "3B": 0.95},
    "MIA": {"HR": 0.93, "1B": 0.99, "2B": 0.98, "3B": 0.90},
    "OAK": {"HR": 0.92, "1B": 0.98, "2B": 0.98, "3B": 0.90},  # Athletics moved; update for 2026
}

# TODO: update for any 2025/2026 stadium changes (Athletics relocation, etc.)
PARK_FACTORS_2026 = PARK_FACTORS_2024.copy()

_NEUTRAL = {"HR": 1.0, "1B": 1.0, "2B": 1.0, "3B": 1.0}


def get_park_factors(home_team: str, year: int = 2026) -> dict:
    """
    Return park factor multipliers for the given home team's stadium.
    Falls back to neutral (1.0) for unknown teams.
    """
    factors = PARK_FACTORS_2026 if year >= 2026 else PARK_FACTORS_2024
    return factors.get(home_team, _NEUTRAL.copy())
