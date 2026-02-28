import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"

for d in [RAW_DIR, PROCESSED_DIR, CACHE_DIR]:
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
MIN_EDGE = 0.03          # minimum 3% edge (model prob vs no-vig market prob) to consider a bet

# Regression — separate scales for batters and pitchers.
# Pitchers are the biggest differentiator (starter faces ~21 BF) so they
# need LESS regression to let quality shine through.  Batters each see only
# ~4 PA, so moderate regression keeps noise down while the 9-batter lineup
# average still captures team quality.
BATTER_REGRESSION_SCALE = 0.20   # was 0.40 — reduced to widen spread
PITCHER_REGRESSION_SCALE = 0.08  # was 0.40 — starters are the biggest game differentiator

# Prior-year seeding
PRIOR_YEAR_WEIGHT = 0.70  # how much prior-year PA data counts (0.5 = half, 1.0 = equal to current)
