"""
Park factor data and application.
Source: Baseball HQ park factors (2025 edition).
Values are multiplicative vs. league average (1.0 = perfectly neutral park).

BHQ provides: RUNS, LHB BA, RHB BA, LHB HR, RHB HR, BB, K
This is richer than FanGraphs component-only factors — we now adjust
BB and K rates by park, and have platoon-split HR and BA factors.
"""

# BHQ park factors converted to multiplicative form.
# Empty BHQ cells → 1.00 (neutral). BHQ "5%" → 1.05, "-8%" → 0.92.
#
# Keys: runs, lhb_ba, rhb_ba, lhb_hr, rhb_hr, bb, k
# BA factors are applied to 1B+2B combined (batting average on BIP).
# HR factors are platoon-split (LHB vs RHB).
# BB and K factors adjust walk and strikeout rates directly.
PARK_FACTORS_2024 = {
    # --- American League ---
    "OAK": {  # Sutter Health Stadium (Athletics)
        "runs": 1.10, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.09, "rhb_hr": 1.08, "bb": 1.00, "k": 1.06,
        "HR": 1.09, "1B": 1.00, "2B": 1.00, "3B": 1.00,
    },
    "BAL": {  # Camden Yards
        "runs": 1.06, "lhb_ba": 1.08, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 1.31, "bb": 0.92, "k": 1.00,
        "HR": 1.16, "1B": 1.04, "2B": 1.02, "3B": 0.85,
    },
    "BOS": {  # Fenway Park
        "runs": 1.05, "lhb_ba": 1.10, "rhb_ba": 1.00,
        "lhb_hr": 0.84, "rhb_hr": 0.88, "bb": 1.00, "k": 0.93,
        "HR": 0.86, "1B": 1.04, "2B": 1.10, "3B": 0.80,
    },
    "CWS": {  # Guaranteed Rate Field
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 0.95, "bb": 1.00, "k": 1.00,
        "HR": 0.98, "1B": 1.00, "2B": 1.00, "3B": 0.95,
    },
    "CLE": {  # Progressive Field
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.06, "rhb_hr": 0.79, "bb": 1.00, "k": 1.05,
        "HR": 0.93, "1B": 1.00, "2B": 1.01, "3B": 1.00,
    },
    "DET": {  # Comerica Park
        "runs": 1.06, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.07, "rhb_hr": 1.05, "bb": 1.05, "k": 0.95,
        "HR": 1.06, "1B": 1.00, "2B": 1.00, "3B": 0.95,
    },
    "HOU": {  # Minute Maid Park
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 1.07, "bb": 1.00, "k": 1.05,
        "HR": 1.04, "1B": 1.00, "2B": 1.01, "3B": 0.90,
    },
    "KCR": {  # Kauffman Stadium
        "runs": 1.05, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 1.00, "bb": 1.10, "k": 0.93,
        "HR": 1.00, "1B": 1.00, "2B": 1.00, "3B": 0.95,
    },
    "LAA": {  # Angel Stadium
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 0.94,
        "lhb_hr": 1.05, "rhb_hr": 1.07, "bb": 1.00, "k": 1.05,
        "HR": 1.06, "1B": 0.97, "2B": 1.00, "3B": 0.95,
    },
    "MIN": {  # Target Field
        "runs": 1.07, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 1.00, "bb": 1.06, "k": 1.00,
        "HR": 1.00, "1B": 1.00, "2B": 1.00, "3B": 0.90,
    },
    "NYY": {  # Yankee Stadium
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 0.95,
        "lhb_hr": 1.17, "rhb_hr": 1.16, "bb": 1.00, "k": 1.00,
        "HR": 1.17, "1B": 0.97, "2B": 0.99, "3B": 0.80,
    },
    "SEA": {  # T-Mobile Park
        "runs": 0.83, "lhb_ba": 0.91, "rhb_ba": 0.86,
        "lhb_hr": 0.85, "rhb_hr": 0.88, "bb": 1.00, "k": 1.14,
        "HR": 0.87, "1B": 0.88, "2B": 0.90, "3B": 0.95,
    },
    "TBR": {  # Tropicana Field
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 0.94,
        "lhb_hr": 0.92, "rhb_hr": 1.08, "bb": 1.00, "k": 1.08,
        "HR": 1.00, "1B": 0.97, "2B": 1.00, "3B": 0.90,
    },
    "TEX": {  # Globe Life Field
        "runs": 0.95, "lhb_ba": 1.00, "rhb_ba": 0.95,
        "lhb_hr": 1.13, "rhb_hr": 1.00, "bb": 1.00, "k": 1.05,
        "HR": 1.07, "1B": 0.97, "2B": 1.00, "3B": 1.00,
    },
    "TOR": {  # Rogers Centre
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.95, "rhb_hr": 1.13, "bb": 1.00, "k": 1.00,
        "HR": 1.04, "1B": 1.00, "2B": 1.00, "3B": 0.90,
    },

    # --- National League ---
    "ARI": {  # Chase Field
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.72, "rhb_hr": 0.87, "bb": 1.00, "k": 1.00,
        "HR": 0.80, "1B": 1.01, "2B": 1.02, "3B": 0.95,
    },
    "ATL": {  # Truist Park
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 0.93, "bb": 1.00, "k": 1.00,
        "HR": 0.97, "1B": 1.00, "2B": 1.02, "3B": 0.95,
    },
    "CHC": {  # Wrigley Field
        "runs": 0.92, "lhb_ba": 1.00, "rhb_ba": 0.94,
        "lhb_hr": 0.86, "rhb_hr": 1.00, "bb": 1.00, "k": 1.06,
        "HR": 0.93, "1B": 0.97, "2B": 1.01, "3B": 1.00,
    },
    "CIN": {  # Great American Ball Park
        "runs": 1.05, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.35, "rhb_hr": 1.26, "bb": 1.00, "k": 1.00,
        "HR": 1.31, "1B": 1.00, "2B": 1.05, "3B": 0.98,
    },
    "COL": {  # Coors Field
        "runs": 1.30, "lhb_ba": 1.14, "rhb_ba": 1.17,
        "lhb_hr": 1.00, "rhb_hr": 1.00, "bb": 1.00, "k": 0.89,
        "HR": 1.00, "1B": 1.15, "2B": 1.07, "3B": 1.40,
    },
    "LAD": {  # Dodger Stadium
        "runs": 0.95, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.16, "rhb_hr": 1.35, "bb": 1.00, "k": 1.00,
        "HR": 1.26, "1B": 1.00, "2B": 1.00, "3B": 0.95,
    },
    "MIA": {  # Marlins Park
        "runs": 1.05, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 0.84, "bb": 1.06, "k": 1.00,
        "HR": 0.92, "1B": 1.00, "2B": 0.98, "3B": 0.90,
    },
    "MIL": {  # American Family Field
        "runs": 1.00, "lhb_ba": 0.94, "rhb_ba": 0.95,
        "lhb_hr": 1.16, "rhb_hr": 1.10, "bb": 1.00, "k": 1.14,
        "HR": 1.13, "1B": 0.94, "2B": 1.00, "3B": 1.00,
    },
    "NYM": {  # Citi Field
        "runs": 1.00, "lhb_ba": 0.93, "rhb_ba": 0.94,
        "lhb_hr": 1.00, "rhb_hr": 1.17, "bb": 1.06, "k": 1.05,
        "HR": 1.09, "1B": 0.93, "2B": 1.00, "3B": 0.90,
    },
    "PHI": {  # Citizens Bank Park
        "runs": 1.05, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 1.29, "rhb_hr": 1.10, "bb": 1.00, "k": 1.00,
        "HR": 1.20, "1B": 1.00, "2B": 1.03, "3B": 0.90,
    },
    "PIT": {  # PNC Park
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.86, "rhb_hr": 0.78, "bb": 1.00, "k": 0.92,
        "HR": 0.82, "1B": 1.00, "2B": 1.00, "3B": 0.95,
    },
    "SDP": {  # PETCO Park
        "runs": 0.93, "lhb_ba": 0.94, "rhb_ba": 1.00,
        "lhb_hr": 1.00, "rhb_hr": 1.10, "bb": 1.00, "k": 1.06,
        "HR": 1.05, "1B": 0.97, "2B": 0.99, "3B": 0.95,
    },
    "SFG": {  # Oracle Park
        "runs": 0.91, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.83, "rhb_hr": 0.81, "bb": 0.86, "k": 0.90,
        "HR": 0.82, "1B": 1.00, "2B": 1.00, "3B": 1.00,
    },
    "STL": {  # Busch Stadium
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.95, "rhb_hr": 0.82, "bb": 1.00, "k": 0.93,
        "HR": 0.89, "1B": 1.00, "2B": 1.01, "3B": 0.95,
    },
    "WSN": {  # Nationals Park
        "runs": 1.00, "lhb_ba": 1.00, "rhb_ba": 1.00,
        "lhb_hr": 0.95, "rhb_hr": 1.00, "bb": 0.95, "k": 0.95,
        "HR": 0.98, "1B": 1.00, "2B": 1.00, "3B": 1.00,
    },
}

# TODO: update for any 2025/2026 stadium changes (Athletics relocation, etc.)
PARK_FACTORS_2026 = PARK_FACTORS_2024.copy()

_NEUTRAL = {
    "HR": 1.0, "1B": 1.0, "2B": 1.0, "3B": 1.0,
    "runs": 1.0, "lhb_ba": 1.0, "rhb_ba": 1.0,
    "lhb_hr": 1.0, "rhb_hr": 1.0, "bb": 1.0, "k": 1.0,
}


def get_park_factors(home_team: str, year: int = 2026) -> dict:
    """
    Return park factor multipliers for the given home team's stadium.
    Falls back to neutral (1.0) for unknown teams.
    """
    factors = PARK_FACTORS_2026 if year >= 2026 else PARK_FACTORS_2024
    return factors.get(home_team, _NEUTRAL.copy())
