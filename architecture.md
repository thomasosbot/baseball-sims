# Architecture

## Overview

Monte Carlo baseball simulation that predicts game outcomes by modelling each plate appearance bottom-up using a multiplicative odds-ratio blending method, then compares model win probabilities to sportsbook lines to find +EV bets. Supports three bet types: moneyline, run line (±1.5 spread), and totals (over/under).

## Pipeline

```
[Data Sources]         [Projections]          [Simulation]          [Betting]
pybaseball          →  Marcel projections  →  PA probability    →  Elo blend
  Statcast              (3yr weighted,         model (b*p/l)       (50% sim + 50% Elo)
BHQ CSVs            →   regression, age)                        →  ML edge calc
  (skills metrics)     BHQ blend (50/50)  →  game sim              (model vs market)
MLB Stats API          batter profiles        (MC engine)       →  quarter-Kelly sizing
  (schedules/lineups)  pitcher profiles                            moneyline bets
The Odds API        →  park factors                             →  ROI / CLV / P&L
  (h2h + spreads       closing lines      →  total runs dist   →  totals edge calc
   + totals)            (FanDuel single-bk)   margin dist       →  spread edge calc
                                                                    (run line ±1.5)
                                                                    │
                                              ┌─────────────────────┘
                                              ▼
                                          [Dashboard]
                                          Streamlit + Plotly
                                          dashboard v2 (single-page)
```

**Full pipeline flow:**
`pybaseball data + BHQ CSVs → Marcel projections (3yr weighted) → BHQ blend (50/50) → player profiles (regressed rates + platoon splits) → PA model (multiplicative odds-ratio) → game sim (Monte Carlo) → Elo blend (50/50) → edge detection (model vs market) → Kelly sizing`

## Modules

| Module | Purpose |
|--------|---------|
| `src/data/fetch.py` | Pulls data from Statcast (pybaseball), MLB Stats API (schedules/lineups), and The Odds API (historical + live odds, h2h + totals + spreads). All results cached to `data/cache/` as pickle files. Includes `build_closing_lines()`, `build_closing_totals()`, and `build_closing_spreads()` — all with quality filters: \|odds\| ≥ 100, per-book vig 0-12%, min 3 books per game, final-pair vig validation. Spread lines filtered to standard ±1.5 only. |
| `src/data/process.py` | Aggregates pitch-level Statcast data into per-player PA outcome rates (K%, BB%, HR rate, etc.) with platoon splits. `extract_team_relievers()` identifies relievers per game (first pitcher per side = starter, rest = relievers). `aggregate_team_bullpen_rates()` builds per-team reliever rate DataFrames. `prepare_for_rolling()` sorts PAs chronologically for cumulative tracking (includes `home_team`, `away_team`, `inning_topbot`). |
| `src/data/cumulative.py` | `CumulativeStats` class for rolling-window backtests. Tracks running PA counts per player, snapshots profiles before each game date. Supports Marcel projection seeding via `init_from_marcel()` (preferred) or legacy `init_from_prior_year()`. Also tracks team relievers via `register_reliever()` / `get_team_reliever_rates()` for rolling bullpen profiles. |
| `src/features/batting.py` | Builds batter profiles: regresses observed rates toward league mean based on sample size (Bayesian shrinkage). Outputs rates by platoon split (vs LHP / vs RHP). |
| `src/features/pitching.py` | Same as batting but from the pitcher's perspective (rates allowed). `build_tiered_bullpen_profiles()` splits relievers into high-leverage and low-leverage tiers by quality metric, returns (hi, lo) tuple. |
| `src/features/marcel.py` | Marcel projection system: 3-year weighted history (5/4/3), regression to league average (1200 PA batters, 450 BF pitchers), age adjustment (+0.6%/yr under 29, -0.3%/yr over 29). Produces per-player projected PA outcome rates with platoon splits. `blend_bhq_marcel()` combines Marcel and BHQ skills-based rates at `BHQ_BLEND_WEIGHT=0.50`. Used to seed `CumulativeStats` on day 1 of each season. |
| `src/data/bhq.py` | Loader for Baseball HQ CSV files from `data/raw/bhq/`. Reads hitter stats, pitcher-advanced, and pitcher-bb files by year. Joins on MLBAMID (same player ID used in Statcast). |
| `src/features/bhq_rates.py` | Converts BHQ skills-based metrics into PA outcome rates compatible with the simulation model. Hitter: Ct%→K, BB%→BB, Brl%→HR, SPD→3B, H%/LD%/FB%/GB%→hit distribution. Pitcher: K%→K, BB%→BB, xHR/FB+FB%→HR, GB%/LD%/FB%→batted ball distribution, H%→hit rate. |
| `src/features/park_factors.py` | BHQ park factor table by team. Multiplicative adjustments for HR, 1B, 2B, 3B, BB, K, plus runs factor and platoon splits. |
| `src/features/weather.py` | Weather adjustments for PA outcome probabilities (v1.2). Temperature and wind factors on HR/2B/3B rates, merged into park_factors. Dome/roof detection. Park CF bearing table for compass→field-relative wind conversion. |
| `src/simulation/constants.py` | League average rates, wOBA weights, base advancement probability tables, DP/sac-fly rates, starter usage limits, TTO hit boost multipliers, stolen base rates, wild pitch rate. |
| `src/simulation/pa_model.py` | Combines batter + pitcher profiles via **multiplicative odds-ratio method** (`b*p/l`), applies park factors (including BB/K), normalises to a probability distribution over 8 PA outcomes. |
| `src/simulation/game_sim.py` | Simulates full 9-inning games PA-by-PA. Tracks baserunners, handles walks, sac flies, double plays, extra innings with ghost runner. Applies times-through-order (TTO) penalty: hit rates boosted +10% on 2nd pass, +20% on 3rd+ pass through the lineup vs the starter. **Stolen base attempts** checked before each PA (calibrated to 2024 MLB: ~0.70 SB/team/game, 78% success rate, speed-adjusted). **Wild pitches/passed balls** (~0.30/team/game) advance runners. `monte_carlo_win_probability()` runs N games and returns win%, score distributions, `total_runs_dist` (raw array for over/under probability), and `margin_dist` (home-away run margin array for spread/run line probability). |
| `src/features/elo.py` | Elo team-strength layer: K=20, HFA=24, regression=1/4 toward 1500 between seasons. Produces per-game Elo-based win probabilities. Blended 50/50 with simulation probabilities via `ELO_BLEND_WEIGHT=0.50`. |
| `src/betting/odds.py` | Fetches live moneylines from The Odds API. Converts between American / decimal / implied probability. Strips vig. |
| `src/betting/edge.py` | Compares model probability to no-vig market probability. Applies alpha + confidence shrinkage: `adjusted_prob = market + (alpha * confidence) * (model - market)`. Alpha controls how much to trust the model vs market (ML=0.4, Totals=0.3). `compute_game_confidence()` combines season depth and model-market agreement. Separate edge/confidence thresholds for moneyline and totals. |
| `src/betting/kelly.py` | Quarter-Kelly bet sizing with a hard cap (default 5% of bankroll). Used for both moneyline and totals. |
| `src/backtest/runner.py` | Two modes: `run_backtest()` (full-season profiles, look-ahead) and `run_rolling_backtest()` (cumulative pre-game profiles, no look-ahead). Both use team-specific bullpen profiles and simulate moneyline, run line (spread), and totals bets when historical odds are available. `_attach_spreads()` computes cover probabilities directly from the simulation margin distribution (no heuristics). |
| `src/backtest/metrics.py` | Brier score, log loss, ROI, CLV, calibration tables, bankroll growth tracking. |
| `dashboard/` | **v1 (`app.py`):** Streamlit app with 5 pages: Performance, How It Works, Predictions, Betting, Diagnostics. Custom Plotly template and CSS. **v2 (`v2.py`):** Single-page dashboard with ROI chart + bet tables ($20K bankroll). Deployed at [baseball-sims.streamlit.app](https://baseball-sims-mqdefvx4nq6bt9mpbvcnb7.streamlit.app). Run locally with `streamlit run dashboard/app.py` or `streamlit run dashboard/v2.py`. |

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| **Multiplicative odds-ratio** for PA blending (v0.7) | `P = b*p/l` — the numerator of the full log5 formula. For rates far from 0.5 (HR, BB, K, etc.) it produces nearly identical results to full log5. For rates near 0.5 (OUT ~0.456), full log5 introduces ~7% compression via its denominator, which compounds across 30+ PA per team into meaningful win-probability compression. The multiplicative form avoids this while preserving correct behavior when both sides are average. |
| **8 PA outcome categories** (K, BB, HBP, HR, 3B, 2B, 1B, OUT) | Captures all strategically important outcomes. Finer granularity (e.g. groundout vs flyout) modelled probabilistically within the OUT bucket. |
| **Bayesian regression to the mean** | Small sample sizes are the biggest noise source in baseball. Regression weights vary by outcome stability (K stabilises fast → low weight; 3B is noisy → high weight). |
| **Quarter Kelly** bet sizing | Full Kelly maximises log-growth but assumes perfect calibration. Quarter Kelly reduces variance ~75% while sacrificing only ~25% of expected growth. |
| **10,000 simulations per game** | Gives ±1% precision on win probability. Reduced to 1,000 for backtesting speed. |
| **Starter pulled after 22 BF** (v1.2) | ~7.3 innings of starter (actual 2024 MLB avg), then team-specific tiered bullpen takes over. |
| **Tiered bullpen** (v1.2) | Relievers split into high-leverage (top half by K%-BB%-3×HR%) and low-leverage tiers. Selected per half-inning based on run differential: \|diff\| ≤ 2 → high-lev. Extra innings always high-lev. Better models actual bullpen usage. |
| **Weather adjustments** (v1.2) | Temperature factor on HR/2B/3B (0.2%/°F from 72°F neutral), wind factor on HR (0.8%/mph out, 0.6%/mph in). Merged into park_factors pre-normalization. Dome/roof games exempt. Addresses +0.48 run overshoot from ignoring cold-weather suppression. |
| **Times-through-order penalty** (v0.6) | Hit rates (1B/2B/3B/HR) boosted +10% on 2nd time through, +20% on 3rd+ time. Re-normalised after boost. Only applies vs the starter (not bullpen). Widens gap between aces (who go deep effectively) and weaker starters. |
| **Team-specific bullpen profiles** (v0.6) | Relievers identified per game from Statcast (first pitcher per side = starter, rest = relievers). Reliever rates aggregated per team weighted by BF. Replaces league-average bullpen for ~3 innings/game. |
| **Split regression scales** (v0.7) | Separate `BATTER_REGRESSION_SCALE=0.20` and `PITCHER_REGRESSION_SCALE=0.08` (was single 0.40). Pitchers get much less regression because starters face ~21 BF and are the primary game differentiator. Effective batter weights: K=40, BB=50, HR=80. Effective pitcher weights: K=24, BB=32, HR=48. |
| **Prior-year weight 0.70** (v0.6, replaced by Marcel in v0.9) | Was 0.50→0.70 for stronger early-season priors. Replaced by Marcel 3-year weighted projections which provide much better day-1 profiles. |
| **FanDuel single-book odds** (v0.6) | Uses FanDuel as the single book for closing lines, with median-consensus fallback when FanDuel is missing. Eliminates cross-book cherry-picking of best odds per side. |
| **BHQ park factors** (v1.0) | Replaces FanGraphs component-only factors with BHQ park factor table. Now includes BB (walk rate), K (strikeout rate), and platoon-split HR/BA factors. BB and K are applied in the PA model alongside HR/1B/2B/3B. Runs factor stored but NOT applied to totals (component factors already capture park effects — applying runs factor was double-counting). |
| **Stolen bases** (v1.0) | Pre-PA stolen base attempts calibrated to 2024 MLB (~0.70 SB/team/game, 78% success rate). Attempt rate adjusted by team average BHQ SPD score. Steal of 2B (7% per PA with runner on 1B, 2B empty) and steal of 3B (1.5%, rarer). Caught stealing adds an out. Addresses model underpredicting runs by ~0.5/game — SBs create extra scoring opportunities. |
| **Wild pitches / passed balls** (v1.0) | 0.8% chance per PA with runners on base (~0.30 WP+PB/team/game). All runners advance one base, runner on 3B scores. Another source of "free" runs the model was previously missing. |
| **Improved base advancement** (v1.0) | Updated base advancement probabilities from Statcast/FanGraphs BsR data (2022-2024 avg). Runner on 1B now has 28% chance of reaching 3B on a single (was 0% — aggressive running, gap hits). Runner on 1B scores 56% on doubles (was 45%). These changes increase expected runs per baserunner. |
| **Errors / reached on error** (v1.0) | 1.4% of outs become reached-on-error (~0.55 errors/team/game). Batter goes to 1B, all runners advance one base, runner on 3B scores. No out recorded. The model previously had zero errors — every out was clean, which systematically underpredicted runs by ~0.3-0.4/team/game. |
| **Productive outs** (v1.0) | On non-sac-fly, non-DP outs, runners can advance: 2B→3B (18%) and 1B→2B (11%), representing groundouts to the right side. Previously all runners stayed frozen on regular outs. |
| **Sac fly fix** (v1.0) | SAC_FLY_PROB reduced from 0.30 to 0.13. The old value assumed only fly ball outs reached the sac fly check, but ALL outs do — was producing 4x too many sac fly runs (~1.35/team/game vs MLB reality of ~0.33). |
| **Lineups from Statcast** | Reconstructed from pitch data (first 9 unique batters by `at_bat_number` per side). Avoids 2,400+ API calls per season. |
| **Switch-hitter handling** | Switch hitters (`bats="S"`) bat from the opposite side of the pitcher, selecting the correct platoon split. |
| **FanGraphs replaced by Statcast** | pybaseball's FanGraphs endpoint is broken (403). All player stats derived directly from Statcast pitch data instead. |
| **Historical odds at 22:00 UTC** | Snapshot at ~6pm ET captures near-closing lines for night games. ~180 API calls per season. |
| **Per-book no-vig consensus** (v0.3.1) | Previous approach cherry-picked best home odds from one book and best away odds from another, creating impossible pairs (e.g. -106 / +400). Fix: compute no-vig probability per book (each book's pair is coherent), then take the median across books. Filters: \|American odds\| ≤ 600, vig 0-12%, minimum 3 books per game. |
| **Totals quality controls** (v0.8) | `build_closing_totals()` now mirrors moneyline QC: \|odds\| ≥ 100, per-book vig 0-12%, min 3 books per game, final-pair vig validation. Previously had no vig/min-books filters, causing 20+ games with negative vig and consensus fallback producing garbage odds (-6.5, -0.5). |
| **Confidence-gated edge detection** (v0.8) | Raw model probabilities are shrunk toward market via `adjusted_prob = market + (alpha * confidence) * (model - market)`. Alpha is per-bet-type (ML=0.4, Totals=0.3). Confidence combines (1) season depth: cumulative pitchers tracked 850→1300 maps to 0.2→1.0, and (2) model-market agreement: penalises when model and market disagree on the favourite. |
| **Run line (spread) betting** (v1.2, refined v1.3) | Cover probabilities computed directly from the 10,000-simulation margin distribution (`P(cover -1.5) = count(margin > 1.5) / N`). No heuristic conversion from win probability. **Dog +1.5 only** — favorite -1.5 bets removed after backtesting showed -25.9% ROI (75 bets). Independent dog +1.5 bets (no ML alignment required) are +5.9% ROI (167 bets). |
| **Separate ML/Totals betting params** (v0.9) | Moneyline and totals use independent alpha, min_edge, max_edge, and min_confidence settings. Grid search on v0.9+BHQ CSV found optimal: **ML: α=0.9, edge 7-15%, conf≥0.5** (+1.3% ROI, 232 bets). **Totals: α=0.3, edge 7-15%, conf≥0.0** (+5.1% ROI, 51 bets). High ML alpha works because BHQ improves projections enough to trust model-market disagreements. |
| **Max edge cap** (v0.9) | Edges above 15% are filtered out. Large model-market disagreements (15-20%+) were almost always losers in v0.8 — the market is right when disagreement is that large. Applies to both moneyline and totals via `ML_MAX_EDGE` / `TOTALS_MAX_EDGE`. |
| **Home field advantage** (v0.9) | Additive +2.5% boost to home win probability post-simulation. Model avg home prob was 0.501 vs actual 0.526 — HFA closes this gap. Applied in `_sim_with_lineups()` and `_sim_league_avg()`. Does not affect total runs distribution. |
| **Marcel preseason projections** (v0.9) | Replaces raw prior-year seeding with Tom Tango's Marcel projection system. Uses 3 years of weighted Statcast (5/4/3), regression to league average (1200 PA batters / 450 BF pitchers), and age adjustment. Produces per-player projected rates with platoon splits. Seeded into `CumulativeStats` at `MARCEL_EFFECTIVE_PA=350` pseudo-PAs. For 2024 backtest: 2021+2022+2023 → 2,194 batters, 1,777 pitchers projected (vs 650 batters from single prior year). |
| **BHQ skills-based blend** (v0.9) | Baseball HQ subscription data provides skills-based leading indicators (Ct%, Brl%, xHR/FB, SPD, etc.) that are converted to PA outcome rates and blended 50/50 with Marcel projections. BHQ metrics are calibrated against Statcast actuals (e.g., K = 0.620*(1-Ct%) + 0.065, r=0.747). Coverage: ~530 batters, ~650 pitchers (regulars only); non-covered players fall back to pure Marcel. Joins on MLBAMID. No look-ahead: backtesting 2024 uses BHQ 2023 data. |
| **Elo team-strength layer** (v0.9) | Elo ratings (K=20, HFA=24, regression=1/4) blended 50/50 with MC simulation probabilities. Helps compensate for the structural compression of PA-by-PA simulation (law of large numbers over ~35 PA/team produces model std=0.057 vs market std=0.110 before Elo). |
| **9.4x simulation speedup** (v0.9) | Pre-computed PA outcome arrays replace per-PA sampling. Reduced per-game simulation time from 38s to 4s. |
| **Totals line range filter** (v0.9) | Total lines outside 5.5-14.0 are filtered out as implausible MLB totals. Prevents garbage consensus lines from entering the pipeline. |
| **Moneyline coherence check** (v0.9) | Both-positive American odds pairs (where neither side is a favorite) are rejected. Ensures structurally valid moneyline pairs. |
| **Totals from simulation distribution** (v0.4) | `P(over) = count(total > line) / count(total ≠ line)` from the raw `total_runs_dist` array. Pushes (total == line) are excluded. More accurate than fitting a parametric distribution. |
| **Rolling backtest with prior-year seeding** (v0.4) | `CumulativeStats` tracks running PA counts. Originally seeded with prior-year Statcast at configurable weight. Replaced by Marcel projections in v0.9. |

## Operationalization (v1.1)

| Component | Module | Purpose |
|-----------|--------|---------|
| `src/data/state.py` | State persistence | Saves/loads CumulativeStats, Elo, batter speeds, bankroll between daily runs as pickles in `data/state/` |
| `scripts/init_season.py` | Preseason setup | One-time: builds Marcel+BHQ projections, seeds Elo from prior seasons, saves initial state |
| `scripts/run_daily.py` | Daily pipeline | Fetches lineups + live odds, runs MC simulation, finds edges, outputs picks to `data/daily/YYYY-MM-DD.json`. Supports `--mode early` (projected lineups from team's most recent game) and `--mode late` (confirmed lineups). Enriched JSON output includes lineup names, sim detail histograms (margin + run distributions), weather, park factors, and Elo ratings for the expandable game detail view. |
| `scripts/update_results.py` | Results grading | Fetches yesterday's scores, grades picks (W/L), updates CumulativeStats + Elo from boxscores, tracks P&L |
| `site/generate.py` | Static site generator | Reads daily JSON files, renders Jinja2 templates to `site/public/` (Netlify) |
| `site/templates/` | Jinja2 templates | `base.html` (nav + subscribe + footer), `index.html` (picks + expandable game details), `history.html` (chart + results), `about.html` (model info) |
| `site/static/style.css` | Site styling | Meta-inspired pastel + glassmorphism UI: light gradient background, frosted glass cards, Inter font, sportsbook-colored odds badges, expandable game detail panels with CSS bar charts |
| `site/netlify/functions/subscribe.js` | Newsletter subscribe | Serverless function on Netlify — POSTs email to Resend Contacts API |
| `site/netlify.toml` | Netlify config | Build settings, publish dir, functions dir |
| `src/newsletter/sender.py` | Email newsletter | Fetches subscribers from Resend Contacts API (audience-based), sends daily picks HTML email from `picks@ozzyanalytics.com` |
| `.github/workflows/daily_picks.yml` | Automation | Two-run daily schedule: early (1 PM ET, projected lineups) + late (6 PM ET, confirmed lineups + newsletter). Auto-detects spring training in March. |

**Daily pipeline flow (two-run schedule):**
```
[1 PM ET] Early run (--mode early)
    → update_results.py (grade yesterday, update state)
    → run_daily.py --mode early (projected lineups, simulate, find edges)
    → site/generate.py (rebuild website with "Preliminary" banner)
    → git commit + push

[6 PM ET] Late run (--mode late)
    → run_daily.py --mode late (confirmed lineups, overwrites daily JSON)
    → site/generate.py (rebuild website, "Final" picks)
    → newsletter/sender.py (email subscribers)
    → git commit + push
```

## Deployment

- **GitHub:** [github.com/thomasosbot/baseball-sims](https://github.com/thomasosbot/baseball-sims) (public)
- **Live dashboard:** [baseball-sims.streamlit.app](https://baseball-sims-mqdefvx4nq6bt9mpbvcnb7.streamlit.app) (Streamlit Community Cloud, auto-deploys from `main`)
- **Picks website:** "Ozzy Analytics" — static HTML in `site/public/`, deployed via Netlify (free tier). Meta-inspired pastel + glassmorphism design with per-sportsbook odds badges on pick cards. Newsletter subscription via Netlify serverless function → Resend Contacts API.

## Validation Status

**2025 out-of-sample backtest (v1.3, rolling backtest with weather + tiered bullpen + dog-only run line, fresh $10K bankroll):**
- Brier 0.2429
- ML: 234 bets, **+8.2% ROI** (+$8,503 profit), 48.7% win rate at avg +97 odds
- Run Line (dog +1.5 only): 228 bets, 59.6% win rate, **+2.8% ROI** (+$3,473). Fav -1.5 removed (-25.9% ROI).
- Totals: 37 bets, -10.1% ROI — market is too sharp on totals
- Conclusion: **Moneyline is the primary profit driver. Dog +1.5 run line adds moderate value. Totals market too efficient.**

## Current Limitations (v1.0)

- **Structural simulation compression** — PA-by-PA MC simulation is compressed by the law of large numbers over ~35 PA/team. Model std=0.057 before Elo blending vs market std=0.110. Elo blend (50/50) and alpha shrinkage compensate at the betting layer.
- **BHQ coverage is regulars only** — ~530 batters and ~650 pitchers have BHQ data. Bench players, callups, and September roster expansions fall back to pure Marcel projections.
- **No umpire adjustments**.
- **Odds matching ~76%** — Seoul Series (neutral venue) and some Sunday games miss. Date/team-name matching has edge cases.
- **Totals line selection** — uses FanDuel line with consensus fallback, which may differ from what's available at other books.
- **Static bullpen composition** — relievers are identified from Statcast game data; in a daily pipeline, pre-game bullpen composition isn't known exactly.
- **Marcel age data** — uses Statcast `age_bat`/`age_pit` columns (age at season start). Players missing from all 3 prior years get no Marcel projection (pure league avg fallback).
- **BHQ data is annual** — skills metrics are from prior full-season data (no in-season updates during backtest). Daily pipeline could incorporate current-year BHQ if subscription provides rolling updates.
