"""
MLB league average constants.
Updated for 2024 season from FanGraphs / Baseball Reference.
"""

# wOBA linear weights (2024 FanGraphs)
WOBA_WEIGHTS = {
    "BB":  0.696,
    "HBP": 0.726,
    "1B":  0.883,
    "2B":  1.244,
    "3B":  1.569,
    "HR":  2.004,
}
WOBA_SCALE   = 1.185
LEAGUE_WOBA  = 0.310

# 2024 MLB league average PA outcome rates (per PA)
# Source: Baseball Reference 2024 season totals
LEAGUE_RATES = {
    "K":   0.224,   # strikeout
    "BB":  0.085,   # walk (non-IBB)
    "HBP": 0.011,   # hit by pitch
    "HR":  0.030,   # home run
    "3B":  0.004,   # triple
    "2B":  0.047,   # double
    "1B":  0.143,   # single
    "OUT": 0.456,   # field out (all other outs)
}
assert abs(sum(LEAGUE_RATES.values()) - 1.0) < 0.01

# Base advancement probabilities by hit type and occupied base.
# Keys are base numbers (1=1B, 2=2B, 3=3B), values are destination lists + probabilities.
# "score" means the runner scores; integer means they advance to that base.
BASE_ADVANCEMENT = {
    "1B": {
        1: {"advance_to": [2],          "probs": [1.00]},           # runner on 1B always advances to 2B
        2: {"advance_to": [3, "score"], "probs": [0.40, 0.60]},     # runner on 2B: 40% stops at 3rd, 60% scores
        3: {"advance_to": ["score"],    "probs": [1.00]},            # runner on 3B always scores
    },
    "2B": {
        1: {"advance_to": [3, "score"], "probs": [0.55, 0.45]},     # runner on 1B: 55% to 3rd, 45% scores
        2: {"advance_to": ["score"],    "probs": [1.00]},
        3: {"advance_to": ["score"],    "probs": [1.00]},
    },
    "3B": {
        1: {"advance_to": ["score"],    "probs": [1.00]},
        2: {"advance_to": ["score"],    "probs": [1.00]},
        3: {"advance_to": ["score"],    "probs": [1.00]},
    },
    "HR": {
        1: {"advance_to": ["score"],    "probs": [1.00]},
        2: {"advance_to": ["score"],    "probs": [1.00]},
        3: {"advance_to": ["score"],    "probs": [1.00]},
    },
}

# Sac fly: probability that a runner on 3B scores on a fly-ball out with < 2 outs
SAC_FLY_PROB = 0.30

# Double play: probability of a DP when there is a runner on 1B and < 2 outs
# (conditioned on the out being a ground ball)
DOUBLE_PLAY_PROB        = 0.12
GROUND_BALL_OUT_FRAC    = 0.45   # fraction of field outs that are ground balls

# Starter usage: switch to bullpen after this many batters faced
STARTER_BATTER_LIMIT = 21   # ~7 innings (3 batters/inning × 7)

# Times-Through-Order penalty: hit rate multiplier by TTO pass
# 1st time = baseline, 2nd time = +10% hits, 3rd+ time = +20% hits
TTO_HIT_BOOST = {1: 1.00, 2: 1.10, 3: 1.20}
