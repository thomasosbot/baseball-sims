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
# Source: MLB Baserunning Stats (Statcast / FanGraphs BsR data, 2022-2024 avg)
BASE_ADVANCEMENT = {
    "1B": {
        1: {"advance_to": [2, 3],      "probs": [0.72, 0.28]},     # runner on 1B: 72% to 2B, 28% to 3B (gap hits, aggressive running)
        2: {"advance_to": [3, "score"], "probs": [0.40, 0.60]},     # runner on 2B: 40% stops at 3rd, 60% scores
        3: {"advance_to": ["score"],    "probs": [1.00]},            # runner on 3B always scores
    },
    "2B": {
        1: {"advance_to": [3, "score"], "probs": [0.44, 0.56]},     # runner on 1B: 44% to 3rd, 56% scores
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

# --------------------------------------------------------------------------
# Stolen bases (new — calibrated to 2024 MLB averages)
# --------------------------------------------------------------------------
# 2024 MLB: ~0.70 SB per team per game, ~80% success rate
# With runner on 1B ~13 times/game → attempt rate ~0.07 per PA with runner on 1B
SB_ATTEMPT_RATE_1B  = 0.07    # probability of SB attempt per PA when runner on 1B (2B empty)
SB_ATTEMPT_RATE_2B  = 0.015   # steal of 3B is rare (~20% of all SB attempts)
SB_SUCCESS_RATE     = 0.78    # 2024 MLB SB success rate
SB_SPEED_FACTOR     = 0.008   # success rate adjustment per SPD point above/below 100
LEAGUE_AVG_SPEED    = 100     # BHQ SPD baseline

# --------------------------------------------------------------------------
# Wild pitches / passed balls
# --------------------------------------------------------------------------
# 2024 MLB: ~0.30 WP+PB per team per game, ~38 PA per team → ~0.008 per PA
WILD_PITCH_RATE = 0.008       # probability of WP/PB per PA (advances all runners 1 base)

# --------------------------------------------------------------------------
# Errors (reached on error)
# --------------------------------------------------------------------------
# 2024 MLB: ~0.55 errors/team/game, ~38 PA/team → ~0.014 per PA
# Batter reaches 1B, all runners advance one base (like a single but less
# aggressive advancement). Runner on 3B scores.
ERROR_RATE = 0.014

# --------------------------------------------------------------------------
# Runner advancement on productive outs
# --------------------------------------------------------------------------
# On groundball outs (non-DP), runners can advance:
# Runner on 2B → 3B on groundout to right side (~40% of GB outs)
# Runner on 1B → 2B on groundout to right side (~25% of GB outs, when no DP)
# Source: Retrosheet/FanGraphs baserunning data 2022-2024
PRODUCTIVE_OUT_2B_TO_3B = 0.18   # prob runner on 2B advances to 3B on any out (0.45 GB * 0.40)
PRODUCTIVE_OUT_1B_TO_2B = 0.11   # prob runner on 1B advances to 2B on any out (0.45 GB * 0.25)

# Sac fly: probability that a runner on 3B scores on an out with < 2 outs.
# MLB avg ~0.33 sac flies/team/game, runner on 3B with <2 outs ~2.5x/game → ~0.13.
# Old value (0.30) assumed only fly balls reached this code, but ALL outs do → was 4x too high.
SAC_FLY_PROB = 0.13

# Double play: probability of a DP when there is a runner on 1B and < 2 outs
# (conditioned on the out being a ground ball)
DOUBLE_PLAY_PROB        = 0.12
GROUND_BALL_OUT_FRAC    = 0.45   # fraction of field outs that are ground balls

# Starter usage: switch to bullpen after this many batters faced
STARTER_BATTER_LIMIT = 21   # ~7 innings (3 batters/inning × 7)

# Times-Through-Order penalty: hit rate multiplier by TTO pass
# 1st time = baseline, 2nd time = +10% hits, 3rd+ time = +20% hits
TTO_HIT_BOOST = {1: 1.00, 2: 1.10, 3: 1.20}
