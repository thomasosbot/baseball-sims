# Architecture

## Overview

Monte Carlo baseball simulation that predicts game outcomes by modelling each plate appearance bottom-up using a multiplicative odds-ratio blending method, then compares model win probabilities to sportsbook lines to find +EV bets. Includes both moneyline and totals (over/under) betting.

## Pipeline

```
[Data Sources]         [Feature Eng.]         [Simulation]          [Betting]
pybaseball          →  batter profiles     →  PA probability    →  ML edge calc
  Statcast              (regressed rates)      model (b*p/l)       (model vs market)
MLB Stats API          pitcher profiles   →  game sim           →  quarter-Kelly sizing
  (schedules/lineups)  park factors           (MC engine)          moneyline bets
The Odds API        →  closing lines      →  ─────────────────→  ROI / CLV / P&L
  (h2h + totals)        (per-book no-vig      total runs dist  →  totals edge calc
                         median consensus)                         over/under bets
                                                                    │
                                              ┌─────────────────────┘
                                              ▼
                                          [Dashboard]
                                          Streamlit + Plotly
                                          5-page interactive viz
```

## Modules

| Module | Purpose |
|--------|---------|
| `src/data/fetch.py` | Pulls data from Statcast (pybaseball), MLB Stats API (schedules/lineups), and The Odds API (historical + live odds, h2h + totals). All results cached to `data/cache/` as pickle files. Includes `build_closing_lines()` (per-book no-vig consensus) and `build_closing_totals()`. |
| `src/data/process.py` | Aggregates pitch-level Statcast data into per-player PA outcome rates (K%, BB%, HR rate, etc.) with platoon splits. `extract_team_relievers()` identifies relievers per game (first pitcher per side = starter, rest = relievers). `aggregate_team_bullpen_rates()` builds per-team reliever rate DataFrames. `prepare_for_rolling()` sorts PAs chronologically for cumulative tracking (includes `home_team`, `away_team`, `inning_topbot`). |
| `src/data/cumulative.py` | `CumulativeStats` class for rolling-window backtests. Tracks running PA counts per player, snapshots profiles before each game date. Supports prior-year seeding at configurable weight. Also tracks team relievers via `register_reliever()` / `get_team_reliever_rates()` for rolling bullpen profiles. |
| `src/features/batting.py` | Builds batter profiles: regresses observed rates toward league mean based on sample size (Bayesian shrinkage). Outputs rates by platoon split (vs LHP / vs RHP). |
| `src/features/pitching.py` | Same as batting but from the pitcher's perspective (rates allowed). `build_bullpen_profile()` aggregates reliever rates weighted by BF into a team-level bullpen profile. |
| `src/features/park_factors.py` | Hardcoded 5-year regressed FanGraphs park factors by team. Multiplicative adjustments for HR, 1B, 2B, 3B. |
| `src/simulation/constants.py` | League average rates, wOBA weights, base advancement probability tables, DP/sac-fly rates, starter usage limits, TTO hit boost multipliers. |
| `src/simulation/pa_model.py` | Combines batter + pitcher profiles via **multiplicative odds-ratio method** (`b*p/l`), applies park factors, normalises to a probability distribution over 8 PA outcomes. |
| `src/simulation/game_sim.py` | Simulates full 9-inning games PA-by-PA. Tracks baserunners, handles walks, sac flies, double plays, extra innings with ghost runner. Applies times-through-order (TTO) penalty: hit rates boosted +10% on 2nd pass, +20% on 3rd+ pass through the lineup vs the starter. `monte_carlo_win_probability()` runs N games and returns win%, score distributions, and `total_runs_dist` (raw array for over/under probability). |
| `src/betting/odds.py` | Fetches live moneylines from The Odds API. Converts between American / decimal / implied probability. Strips vig. |
| `src/betting/edge.py` | Compares model probability to no-vig market probability. Flags bets exceeding the minimum edge threshold (default 3%). Used for both moneyline and totals. |
| `src/betting/kelly.py` | Quarter-Kelly bet sizing with a hard cap (default 5% of bankroll). Used for both moneyline and totals. |
| `src/backtest/runner.py` | Two modes: `run_backtest()` (full-season profiles, look-ahead) and `run_rolling_backtest()` (cumulative pre-game profiles, no look-ahead). Both use team-specific bullpen profiles and simulate moneyline and totals bets when historical odds are available. |
| `src/backtest/metrics.py` | Brier score, log loss, ROI, CLV, calibration tables, bankroll growth tracking. |
| `dashboard/` | Streamlit app with 5 pages: **Performance** (hero KPIs, equity curve, monthly P&L), **How It Works** (plain-English model explainer with illustrative charts), **Predictions** (reliability diagram, calibration residuals, Brier by month, score-diff box plots), **Betting** (ML/totals bet tables, ROI breakdowns, drawdown, Kelly distribution, team performance), **Diagnostics** (rolling vs full comparison, knowledge growth, profile coverage, predicted vs actual runs). Custom Plotly template and CSS. Deployed at [baseball-sims.streamlit.app](https://baseball-sims-mqdefvx4nq6bt9mpbvcnb7.streamlit.app). Run locally with `streamlit run dashboard/app.py`. |

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Multiplicative odds-ratio** for PA blending (v0.7) | `P = b*p/l` — the numerator of the full log5 formula. For rates far from 0.5 (HR, BB, K, etc.) it produces nearly identical results to full log5. For rates near 0.5 (OUT ~0.456), full log5 introduces ~7% compression via its denominator, which compounds across 30+ PA per team into meaningful win-probability compression. The multiplicative form avoids this while preserving correct behavior when both sides are average. |
| **8 PA outcome categories** (K, BB, HBP, HR, 3B, 2B, 1B, OUT) | Captures all strategically important outcomes. Finer granularity (e.g. groundout vs flyout) modelled probabilistically within the OUT bucket. |
| **Bayesian regression to the mean** | Small sample sizes are the biggest noise source in baseball. Regression weights vary by outcome stability (K stabilises fast → low weight; 3B is noisy → high weight). |
| **Quarter Kelly** bet sizing | Full Kelly maximises log-growth but assumes perfect calibration. Quarter Kelly reduces variance ~75% while sacrificing only ~25% of expected growth. |
| **10,000 simulations per game** | Gives ±1% precision on win probability. Reduced to 1,000 for backtesting speed. |
| **Starter pulled after 21 BF** | ~7 innings of starter, then team-specific bullpen takes over. |
| **Times-through-order penalty** (v0.6) | Hit rates (1B/2B/3B/HR) boosted +10% on 2nd time through, +20% on 3rd+ time. Re-normalised after boost. Only applies vs the starter (not bullpen). Widens gap between aces (who go deep effectively) and weaker starters. |
| **Team-specific bullpen profiles** (v0.6) | Relievers identified per game from Statcast (first pitcher per side = starter, rest = relievers). Reliever rates aggregated per team weighted by BF. Replaces league-average bullpen for ~3 innings/game. |
| **Split regression scales** (v0.7) | Separate `BATTER_REGRESSION_SCALE=0.20` and `PITCHER_REGRESSION_SCALE=0.08` (was single 0.40). Pitchers get much less regression because starters face ~21 BF and are the primary game differentiator. Effective batter weights: K=40, BB=50, HR=80. Effective pitcher weights: K=24, BB=32, HR=48. |
| **Prior-year weight 0.70** (v0.6) | Increased from 0.50 to give stronger priors in rolling backtest early-season. 500 prior-year PA → 350 effective (was 250). |
| **FanDuel single-book odds** (v0.6) | Uses FanDuel as the single book for closing lines, with median-consensus fallback when FanDuel is missing. Eliminates cross-book cherry-picking of best odds per side. |
| **Park factors hardcoded (2024)** | FanGraphs 5-year regressed factors are stable year-to-year. |
| **Lineups from Statcast** | Reconstructed from pitch data (first 9 unique batters by `at_bat_number` per side). Avoids 2,400+ API calls per season. |
| **Switch-hitter handling** | Switch hitters (`bats="S"`) bat from the opposite side of the pitcher, selecting the correct platoon split. |
| **FanGraphs replaced by Statcast** | pybaseball's FanGraphs endpoint is broken (403). All player stats derived directly from Statcast pitch data instead. |
| **Historical odds at 22:00 UTC** | Snapshot at ~6pm ET captures near-closing lines for night games. ~180 API calls per season. |
| **Per-book no-vig consensus** (v0.3.1) | Previous approach cherry-picked best home odds from one book and best away odds from another, creating impossible pairs (e.g. -106 / +400). Fix: compute no-vig probability per book (each book's pair is coherent), then take the median across books. Filters: \|American odds\| ≤ 600, vig 0-12%, minimum 3 books per game. |
| **Totals from simulation distribution** (v0.4) | `P(over) = count(total > line) / count(total ≠ line)` from the raw `total_runs_dist` array. Pushes (total == line) are excluded. More accurate than fitting a parametric distribution. |
| **Rolling backtest with prior-year seeding** (v0.4) | `CumulativeStats` tracks running PA counts. Prior-year Statcast seeded at weight=0.5 (500 prior PA → 250 effective). Eliminates look-ahead bias. |

## Deployment

- **GitHub:** [github.com/thomasosbot/baseball-sims](https://github.com/thomasosbot/baseball-sims) (public)
- **Live dashboard:** [baseball-sims.streamlit.app](https://baseball-sims-mqdefvx4nq6bt9mpbvcnb7.streamlit.app) (Streamlit Community Cloud, auto-deploys from `main`)

## Current Limitations (v0.7)

- **Full rolling backtest not yet run** — v0.7 smoke test (50 games) shows std=0.108 vs market std=0.110. Full 2,400+ game backtest needed to confirm spread, calibration, and profitability.
- **Rolling backtest early-season compression** — April/May predictions still somewhat compressed because current-year samples are tiny and prior-year data is discounted (even with 0.70 weight).
- **No weather or umpire adjustments**.
- **Odds matching ~76%** — Seoul Series (neutral venue) and some Sunday games miss. Date/team-name matching has edge cases.
- **Totals line selection** — uses FanDuel line with consensus fallback, which may differ from what's available at other books.
- **Static bullpen composition** — relievers are identified from Statcast game data; in a daily pipeline, pre-game bullpen composition isn't known exactly.
