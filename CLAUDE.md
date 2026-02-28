# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

MLB baseball simulation model that predicts game outcomes using Statcast data and Monte Carlo simulation. The model compares its win probabilities to sportsbook lines to find +EV bets for the 2026 MLB season. The repo also doubles as an Obsidian vault for planning notes.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Populate the data cache (run first — fetches 2021-2024 from pybaseball)
python scripts/fetch_data.py
python scripts/fetch_data.py --years 2023 2024   # specific years only
python scripts/fetch_data.py --statcast           # include Statcast (~10 min/year)
python scripts/fetch_data.py --odds               # fetch historical odds (~180 API calls/year)

# Run historical backtest
python scripts/backtest.py                        # full backtest (slow, ~67 min/year)
python scripts/backtest.py --quick                # 50 games/year smoke test
python scripts/backtest.py --sims 1000            # fewer sims for speed
python scripts/backtest.py --bankroll 10000       # set starting bankroll for bet sim

# Daily betting pipeline (requires ODDS_API_KEY in .env)
python scripts/run_daily.py --bankroll 1000
```

## Architecture

The model is a bottom-up Monte Carlo simulation. For each plate appearance, batter and pitcher outcome profiles are blended using the **multiplicative odds-ratio method** (`b*p/l`), adjusted for park factors, then a PA outcome is sampled. Full games are simulated 10,000 times to produce win probability distributions.

**Pipeline:** `pybaseball data → player profiles (regressed rates + platoon splits) → PA model (multiplicative odds-ratio) → game sim (Monte Carlo) → edge detection (model vs market) → Kelly sizing`

Key modules:

| Layer | Modules |
|-------|---------|
| Data | `src/data/fetch.py` (Statcast, MLB Stats API, historical odds), `src/data/process.py` (Statcast → PA rates) |
| Features | `src/features/batting.py`, `pitching.py` (Bayesian regression), `park_factors.py` |
| Simulation | `src/simulation/constants.py` (league avgs), `pa_model.py` (multiplicative odds-ratio), `game_sim.py` (MC engine) |
| Betting | `src/betting/odds.py` (The Odds API), `edge.py`, `kelly.py` (quarter-Kelly sizing) |
| Backtest | `src/backtest/runner.py`, `metrics.py` (Brier, CLV, ROI, calibration) |

## Context Files

These markdown files are maintained as living documentation:
- `architecture.md` — system design, pipeline diagram, key decisions, limitations
- `datasources.md` — API endpoints, rate limits, field descriptions
- `features.md` — every model feature, how it's calculated, why it's included
- `backtest_results.md` — running log of model iterations and performance metrics

Update these files whenever the model architecture, data sources, features, or backtest results change.

## Key Design Decisions

- **8 PA outcome categories**: K, BB, HBP, HR, 3B, 2B, 1B, OUT
- **Bayesian regression to the mean**: outcome-specific weights with split scales (batter 0.20, pitcher 0.08)
- **Quarter Kelly** bet sizing with 5% hard cap and 3% minimum edge threshold
- **Starter pulled after 21 BF** (~7 innings), then team-specific bullpen profile takes over
- All data cached to `data/cache/` as pickle files — delete cache to re-fetch

## Obsidian

The `.obsidian/` directory contains vault configuration. Don't edit those files manually.
