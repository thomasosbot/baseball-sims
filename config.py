import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"

STATE_DIR = DATA_DIR / "state"
DAILY_DIR = DATA_DIR / "daily"

for d in [RAW_DIR, PROCESSED_DIR, CACHE_DIR, STATE_DIR, DAILY_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# API keys
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

# Simulation settings
N_SIMULATIONS = 10_000
RANDOM_SEED = 42

# Season
SEASON_YEAR = 2026
BACKTEST_START_YEAR = 2021
BACKTEST_END_YEAR = 2024

# Bet sizing
KELLY_FRACTION = 0.25   # quarter Kelly — full Kelly is theoretically optimal but too aggressive in practice
MAX_BET_FRACTION = 0.05  # hard cap: never more than 5% of bankroll on one game
# --- Moneyline betting parameters ---
ML_ALPHA = 0.9            # shrinkage toward market (0=pure market, 1=pure model) — grid search optimal
ML_MIN_EDGE = 0.07        # minimum edge to bet — 7% filters noise, 3-5% sweet spot unreliable
ML_MAX_EDGE = 0.15        # cap: edges above this are market-is-right territory
ML_MIN_CONFIDENCE = 0.5   # minimum game confidence — filters weak early-season bets

# --- Totals betting parameters ---
TOTALS_ALPHA = 0.3        # heavy market deference — totals market is efficient
TOTALS_MIN_EDGE = 0.07    # 7% threshold — grid search optimal
TOTALS_MAX_EDGE = 0.15
TOTALS_MIN_CONFIDENCE = 0.0

# --- Home field advantage ---
HOME_FIELD_ADVANTAGE = 0.025  # +2.5% additive boost to home win prob

# Legacy aliases (backward compat for analyze_edges.py etc.)
MIN_EDGE = ML_MIN_EDGE
MIN_CONFIDENCE = ML_MIN_CONFIDENCE
MAX_EDGE = ML_MAX_EDGE

# Regression — separate scales for batters and pitchers.
# Pitchers are the biggest differentiator (starter faces ~21 BF) so they
# need LESS regression to let quality shine through.  Batters each see only
# ~4 PA, so moderate regression keeps noise down while the 9-batter lineup
# average still captures team quality.
BATTER_REGRESSION_SCALE = 0.20   # was 0.40 — reduced to widen spread
PITCHER_REGRESSION_SCALE = 0.08  # was 0.40 — starters are the biggest game differentiator

# Prior-year seeding (legacy fallback)
PRIOR_YEAR_WEIGHT = 0.70  # how much prior-year PA data counts (0.5 = half, 1.0 = equal to current)

# Marcel projection settings
MARCEL_WEIGHTS = (5, 4, 3)           # year weights (most recent first)
MARCEL_BATTER_REGRESSION = 1200      # PA denominator for batter regression
MARCEL_PITCHER_REGRESSION = 450      # BF denominator for pitcher regression
MARCEL_EFFECTIVE_PA = 1200           # pseudo-PA weight — high because Marcel is already regressed
MARCEL_AGE_PEAK = 29
MARCEL_AGE_YOUNG_RATE = 0.006       # +0.6%/yr improvement under peak
MARCEL_AGE_OLD_RATE = 0.003         # -0.3%/yr decline over peak

# BHQ integration
BHQ_BLEND_WEIGHT = 0.50            # 0=pure Marcel, 1=pure BHQ skills rates

# Elo / team-strength blending
ELO_BLEND_WEIGHT = 0.50            # fraction of final prob from Elo (rest from sim)
