# Features

Every feature used in the simulation model, how it's calculated, and why.

## PA Outcome Rates (core features)

These are the 8 mutually exclusive outcomes of every plate appearance. The simulation draws from this distribution for each PA.

| Feature      | Calculation                            | Why included                                                                                                                    |
| ------------ | -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **K rate**   | Strikeouts / PA                        | Strikeouts eliminate baserunner advancement and double-play risk. High-K pitchers suppress offence independently of BABIP luck. |
| **BB rate**  | (Walks + IBB) / PA                     | Walks are the most stable offensive skill and guaranteed baserunners.                                                           |
| **HBP rate** | Hit-by-pitch / PA                      | Low-frequency but non-trivial (some batters crowd the plate). Regressed heavily toward league average (~1.1%).                  |
| **HR rate**  | Home runs / PA                         | The most impactful single offensive event. Driven by exit velocity + launch angle. Park factor adjusted.                        |
| **3B rate**  | Triples / PA                           | Rare (~0.4% league avg) and extremely noisy — heavily regressed. Driven by speed + park dimensions.                             |
| **2B rate**  | Doubles / PA                           | Second-most impactful hit type. Park-adjusted (Fenway = doubles machine).                                                       |
| **1B rate**  | Singles / PA = (H - HR - 3B - 2B) / PA | Residual hit type. Influenced by BABIP and batted ball profile.                                                                 |
| **OUT rate** | 1 - all of the above                   | Catch-all for field outs (groundout, flyout, lineout, etc.). Sub-categorised probabilistically for DP and sac-fly modelling.    |

### How rates are derived

1. **From Statcast** (`src/data/process.py`): aggregate pitch-level `events` column into PA outcomes per player. Gives the most granular data including platoon splits.
2. ~~From FanGraphs~~ — no longer used. pybaseball's FanGraphs endpoint is broken (403). All stats derived from Statcast.

### Platoon splits

Each rate is computed separately for:
- **Batters**: vs RHP and vs LHP
- **Pitchers**: vs RHB and vs LHB

This captures the well-documented platoon advantage (batters hit better against opposite-hand pitchers).

## Regression to the Mean

All rates are Bayesian-regressed toward the league mean before entering the simulation. This is the single biggest factor compressing the model's probability spread — see [Known Issue: Narrow Spread](#known-issue-narrow-probability-spread) below.

### Batter regression weights (`src/features/batting.py`)

| Outcome | Regression weight (PA) | Rationale |
|---------|----------------------|-----------|
| K | 200 | Strikeout rate stabilises quickly — it's one of the most reliable batting skills |
| BB | 250 | Walk rate is also stable but slightly noisier than K |
| HBP | 500 | Very noisy at the player level; small counts mean large variance |
| HR | 400 | HR rate has meaningful signal but is influenced by ballpark and batted-ball luck |
| 3B | 800 | Extremely rare event; almost entirely regressed to league average for most players |
| 2B | 500 | Moderate stability; influenced by park more than pure skill |
| 1B | 400 | Residual category; influenced by BABIP which is notoriously noisy |
| OUT | 300 | Moderately stable as the complement of all other rates |

### Pitcher regression weights (`src/features/pitching.py`)

Pitchers use 1.5-2x larger weights because pitcher outcome rates are noisier (higher BABIP variance, smaller split samples).

| Outcome | Regression weight (BF) |
|---------|----------------------|
| K | 300 |
| BB | 400 |
| HBP | 800 |
| HR | 600 |
| 3B | 1200 |
| 2B | 700 |
| 1B | 600 |
| OUT | 500 |

**Formula**: `posterior = (observed * n + league_avg * weight) / (n + weight)`

### Regression scales (v0.7)

Regression weights are multiplied by separate scales for batters and pitchers:
- `config.BATTER_REGRESSION_SCALE` = **0.20** (was 0.40 in v0.6)
- `config.PITCHER_REGRESSION_SCALE` = **0.08** (was 0.40 in v0.6)

**Why split?** Pitchers are the primary game differentiator — a starter faces ~21 BF per game, so pitcher quality is the largest single input. With less regression, ace vs. back-end-rotation differences survive into the model. Batters each see only ~4 PA individually, but the 9-batter lineup average still captures team quality with moderate regression.

**Effective batter weights** at 0.20 scale: K=40, BB=50, HBP=100, HR=80, 3B=160, 2B=100, 1B=80, OUT=60.
**Effective pitcher weights** at 0.08 scale: K=24, BB=32, HBP=64, HR=48, 3B=96, 2B=56, 1B=48, OUT=40.

**Impact:** At old settings (0.40/0.40), compound regression of both batter AND pitcher toward the same league mean killed spread — only 68% of scoring spread was preserved early season. At new settings (0.20/0.08), model probability std matches market std within 2%.

### Rolling backtest priors

In the rolling-window backtest (`src/data/cumulative.py`), prior-year Statcast data is seeded at `config.PRIOR_YEAR_WEIGHT` (default 0.70, increased from 0.50 in v0.6). 500 prior-year PA → 350 effective PA. This means early-season predictions are still somewhat compressed because:
- Prior-year data is discounted 30%
- Current-year PA counts are tiny in April
- Regression weights further pull toward the mean

The increased weight (0.70 vs 0.50) gives stronger priors, especially beneficial for early-season differentiation.

## Multiplicative Odds-Ratio Blending (v0.7)

When a specific batter faces a specific pitcher, their individual rates are combined using the multiplicative odds-ratio formula:

```
P(outcome | batter, pitcher) = b × p / l
```

Where `b` = batter rate, `p` = pitcher rate, `l` = league rate.

This is the numerator of the full log5 formula. The full log5 formula adds a denominator: `(b*p/l) + ((1-b)*(1-p)/(1-l))`. For rates far from 0.5 (HR ~3%, K ~22%, BB ~8.5%), the denominator is ≈1.0 and produces nearly identical results. For rates near 0.5 (OUT ~0.456), the full log5 denominator introduces ~7% compression. This compression compounds across 30+ PA per team into meaningful win-probability compression.

**Why multiplicative**: The simpler formula avoids the systematic compression on the OUT rate without sacrificing accuracy on the skill-based outcomes. It also preserves the correct behavior: when both batter and pitcher are league-average, the result is league-average. When one is above and the other below, the result is between them. The distribution is re-normalised after park factor adjustments, so the probabilities always sum to 1.

## Park Factors

Multiplicative adjustments applied to HR, 3B, 2B, and 1B rates after log5 blending.

| Feature | Source | Why included |
|---------|--------|--------------|
| **HR park factor** | FanGraphs 5-year regressed | Coors (+22%) vs. Oracle Park (-5%) is a massive difference in HR production |
| **2B park factor** | FanGraphs 5-year regressed | Fenway's wall generates +10% more doubles |
| **3B park factor** | FanGraphs 5-year regressed | Large outfields (e.g. old Coors) produce more triples |
| **1B park factor** | FanGraphs 5-year regressed | Smaller effect but still measurable |

## Bullpen Modelling (v0.6)

After the starter is pulled (21 BF), a **team-specific bullpen profile** takes over for the rest of the game (~3 innings).

**Implementation:**
1. `extract_team_relievers()` in `process.py` identifies relievers per game from Statcast — first pitcher per side = starter, all others = relievers
2. `aggregate_team_bullpen_rates()` filters Statcast to reliever PAs only (merged on pitcher_id + game_pk), aggregates per-pitcher rates with `min_bf=10`, then maps pitchers to teams
3. `build_bullpen_profile()` in `pitching.py` averages reliever rates weighted by BF, with platoon splits, normalised to sum to 1
4. In the backtest, team bullpen profiles are passed to `_sim_with_lineups()`, which looks up `team_bullpen_profiles[team]` with fallback to `_DEFAULT_PITCHER` (league-average)

**Rolling backtest:** `CumulativeStats` tracks relievers via `register_reliever(pitcher_id, team)` as each day's games are processed. `get_team_reliever_rates()` exports per-team reliever rate DataFrames on demand.

**Impact:** The Dodgers' elite bullpen and the Rockies' terrible bullpen are now differentiated, adding ~3 innings of team-specific data per game. All 30 MLB teams have bullpen profiles in 2024 Statcast data.

## Times-Through-Order Penalty (v0.6)

Starters get progressively worse as they face the same batters repeatedly. The TTO penalty models this.

**Implementation** (`game_sim.py`):
- `batter_tto` dict tracks how many times each lineup slot has faced the starter
- On the 2nd time through: 1B/2B/3B/HR probabilities multiplied by 1.10 (+10%), then re-normalised
- On the 3rd+ time through: multiplied by 1.20 (+20%)
- Only applies while facing the starter (not bullpen)
- TTO tracking is not passed into extra innings (starters are already out)

**Constants** (`constants.py`): `TTO_HIT_BOOST = {1: 1.00, 2: 1.10, 3: 1.20}`

**Impact:** Widens the gap between ace starters (who go deep effectively before being pulled) and weaker starters (who allow more damage the 3rd time through). Also increases late-inning run production realism.

## Base-State Modelling Features

These are not player features but rather game-state parameters that affect the simulation.

| Feature | Value | Source |
|---------|-------|--------|
| Sac fly probability | 30% (runner on 3B, fly out, < 2 outs) | Historical Retrosheet data |
| Double play probability | 12% (runner on 1B, ground ball, < 2 outs) | Historical DP rates |
| Ground ball fraction of outs | 45% | League average GB/FB split |
| Starter batter limit | 21 batters faced | ~7 innings; proxy for when bullpen takes over |

## Betting Features

### Moneyline odds

- **Source**: The Odds API historical endpoint, FanDuel as single book (consensus fallback)
- **Closing line construction**: FanDuel odds for both home and away (coherent pair from one book). Filters: |American odds| ≤ 600, vig 0-12%, minimum 3 books available per game. Falls back to median-consensus when FanDuel is missing.
- **Edge detection**: `model_prob - market_no_vig_prob`, minimum 3% edge to bet
- **Sizing**: Quarter-Kelly with 5% hard cap

### Totals (over/under)

- **Source**: Same API, totals market. Consensus line = mode across books, FanDuel odds on that line (median fallback).
- **Model probability**: From MC simulation distribution — `P(over) = count(total > line) / count(total ≠ line)` (pushes excluded)
- **Edge & sizing**: Same 3% threshold and quarter-Kelly as moneyline

## Probability Spread (resolved in v0.7)

The model's predicted win probabilities were historically narrower than the market's range (~20-80%). This was the #1 issue through v0.6. As of v0.7, the model's probability std (0.108) matches the market's (0.110) within 2%.

**Addressed across v0.6-v0.7:**
1. ~~Heavy regression weights~~ → Split scales: batter 0.20, pitcher 0.08 (v0.7)
2. ~~League-average bullpen~~ → Team-specific bullpen profiles (v0.6)
3. ~~No times-through-order penalty~~ → +10%/+20% hit boost for 2nd/3rd TTO (v0.6)
4. ~~Log5 double-compression~~ → Multiplicative `b*p/l` removes ~7% OUT compression (v0.7)

**Remaining cause:**
5. **Rolling backtest early-season compression** — April/May predictions still somewhat compressed because current-year samples are tiny and prior-year data is discounted (even with 0.70 weight). Preseason projections would help.

## Improvement Roadmap

### v0.6 — Widen probability spread + better priors (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **Team-specific bullpen profiles** | `process.py`, `pitching.py`, `runner.py`, `cumulative.py` | Done — 30 team bullpens, wired into both backtest modes |
| **Times-through-order penalty** | `constants.py`, `game_sim.py` | Done — +10% / +20% hit boost for 2nd/3rd TTO |
| **Prior-year weight 0.70** | `config.py`, `runner.py` | Done — stronger early-season priors |
| **FanDuel single-book odds** | `fetch.py` | Done — eliminates cross-book cherry-picking |

### v0.7 — Split regression + multiplicative PA model (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **Split regression scales** | `config.py`, `batting.py`, `pitching.py` | Done — batter 0.20, pitcher 0.08 (was single 0.40) |
| **Multiplicative PA model** | `pa_model.py` | Done — `b*p/l` replaces full log5, removes ~7% OUT compression |

### v0.8 — Better priors and context (next)

| Change | Module | Expected impact |
|--------|--------|-----------------|
| **Preseason projection priors** | `cumulative.py`, `batting.py` | Use Steamer/ZiPS projections (regression-adjusted, include aging curves) instead of raw prior-year Statcast. Better early-season predictions. |
| **Lineup-order weighting** | `game_sim.py` | Batting order determines PA frequency — the 1-4 hitters get ~15% more PA than 7-9 hitters. Currently all 9 batters cycle equally. |
| **Platoon-aware bullpen** | `game_sim.py` | Switch to LHP/RHP reliever based on batter handedness. Currently bullpen profile is a single aggregate regardless of matchup. |

### v0.9+ — Additional features

| Feature | Expected impact |
|---------|-----------------|
| **Sprint speed** | Affects base advancement probabilities (fast runner on 2B scores on a single more often) |
| **Catcher framing** | Shifts K/BB rates by ~1-2% for elite/poor framers |
| **Umpire tendencies** | Some umps have measurably larger/smaller strike zones |
| **Weather (wind, temp)** | Wind out at Wrigley can add 1-2 runs to expected total |
| **Bullpen availability** | Back-to-back usage degrades reliever quality; bullpen games vs. traditional starts |
| **Batter vs specific pitcher history** | Only useful with 50+ PA sample (rare); mostly noise |
