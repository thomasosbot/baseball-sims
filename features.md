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

### Preseason priors: Marcel projections (v0.9)

In the rolling-window backtest, day-1 player profiles are seeded from **Marcel projections** (`src/features/marcel.py`) instead of raw prior-year data. Marcel uses Tom Tango's open formula:

1. **3-year weighted history**: weights 5/4/3 (most recent heaviest). If only 2 or 1 years are available, uses what's there.
2. **Regression to league average**: `player_weight = PA / (PA + 1200)` for batters, `BF / (BF + 450)` for pitchers. Players with few PA regress heavily; high-PA players keep their rates.
3. **Age adjustment**: contact-quality rates (HR, 2B, 3B, 1B) multiplied by `1 + 0.006 * (29 - age)` for young players, `1 - 0.003 * (age - 29)` for older players. K/BB/HBP not age-adjusted. Pitchers get inverted adjustment (young pitchers improve = lower rates allowed).
4. **Platoon splits**: each split (vs LHP/RHP for batters, vs LHB/RHB for pitchers) is projected separately with the same weighting/regression.

Marcel projections are loaded into `CumulativeStats` at `MARCEL_EFFECTIVE_PA=350` pseudo-PAs. As real current-year PAs accumulate at weight 1.0, they gradually overtake the Marcel prior.

**Impact**: For the 2024 backtest, Marcel projects 2,194 batters and 1,777 pitchers from 2021+2022+2023 data. The old single-year approach only covered 650 batters from 2023 alone. Multi-year weighting stabilises projections (a player's one bad year is balanced by adjacent seasons) and the age curve properly handles aging stars and improving young players.

**Config**: `MARCEL_WEIGHTS`, `MARCEL_BATTER_REGRESSION`, `MARCEL_PITCHER_REGRESSION`, `MARCEL_EFFECTIVE_PA`, `MARCEL_AGE_PEAK`, `MARCEL_AGE_YOUNG_RATE`, `MARCEL_AGE_OLD_RATE` — all in `config.py`.

### BHQ Skills-Based Rates (v0.9)

Baseball HQ provides skills-based leading indicators that complement Marcel's counting-stat projections. BHQ metrics measure underlying skills (contact quality, plate discipline, speed) rather than outcomes, making them more predictive of future performance.

**Loader:** `src/data/bhq.py` — reads CSV files from `data/raw/bhq/` by year, indexed by MLBAMID.
**Converter:** `src/features/bhq_rates.py` — transforms BHQ metrics into the 8 PA outcome rates.
**Blender:** `src/features/marcel.py:blend_bhq_marcel()` — combines BHQ and Marcel at `BHQ_BLEND_WEIGHT=0.50`.

#### Hitter BHQ Features

| BHQ Metric | Target Rate | Correlation | Calibration Formula | Rationale |
|------------|-------------|-------------|---------------------|-----------|
| **Ct%** (contact rate) | K rate | r=0.747 | `K = 0.620 * (1 - Ct%) + 0.065` | Contact rate is the strongest predictor of strikeout rate; players who make contact don't strike out |
| **BB%** | BB rate | r=0.608 | Direct (BB% / 100) | Plate discipline metric; directly measures walk tendency |
| **Brl%** (barrel rate) | HR rate | r=0.526 | `HR/BIP = 0.015 + 0.30 * Brl%` | Barrels (optimal exit velo + launch angle) are the strongest HR predictor; more stable than HR/FB |
| **SPD** (speed score) | 3B rate | r=0.296 | Scaled to league average | Speed is the primary driver of triples; park effects handled separately |
| **H%** (BABIP) | Hit rate on BIP | — | Direct | Batting average on balls in play; used with batted ball profile for 1B/2B split |
| **LD%**, **FB%**, **GB%** | 1B/2B distribution | — | Batted ball profile | Line drives produce more extra-base hits; ground balls produce more singles |
| **xBA**, **PX**, **HctX** | Fallback indicators | — | Used when primary metrics missing | Expected batting average, power index, and hard-contact index as secondary signals |

#### Pitcher BHQ Features

| BHQ Metric | Target Rate | Correlation | Calibration Formula | Rationale |
|------------|-------------|-------------|---------------------|-----------|
| **K%** | K rate | r=0.475 | Direct (K% / 100) | Strikeout percentage is a true skill metric for pitchers |
| **SwK%** (swinging-K rate) | K rate (fallback) | — | Used when K% missing | Measures swing-and-miss ability independent of called strikes |
| **BB%** | BB rate | r=0.320 | Direct (BB% / 100) | Command metric; lower correlation than hitters because pitcher BB% is noisier |
| **xHR/FB** + **FB%** | HR rate | r=0.310 | BHQ scale: 1.0 = 10% HR/FB | Expected HR/FB rate combined with fly ball tendency; more stable than raw HR rate |
| **GB%**, **LD%**, **FB%** | Batted ball distribution | — | Direct | Ground-ball pitchers suppress extra-base hits; fly-ball pitchers allow more HR |
| **H%** (BABIP) | Hit rate on BIP | — | Direct | Pitcher BABIP; partially skill-based (batted ball quality) |

#### Coverage and Fallback

- **Hitters:** ~530 per year (regulars with enough PA for BHQ to publish)
- **Pitchers:** ~650 per year (starters + high-usage relievers)
- Players without BHQ data receive pure Marcel projections (no BHQ blend)
- **No look-ahead:** Backtesting 2024 uses BHQ 2023 data only

**Config:** `BHQ_BLEND_WEIGHT = 0.50` in `config.py`.

### Elo Team-Strength Layer (v0.9)

An Elo rating system provides a team-level strength signal that is blended with the PA-by-PA simulation output.

**Implementation:** `src/features/elo.py`
- K-factor: 20 (standard for baseball)
- Home-field advantage: 24 Elo points
- Between-season regression: 1/4 toward 1500
- Preseason spread: std=45.2 (top team LAD home vs avg: 0.631, bottom CWS: 0.385)

**Blending:** `final_prob = ELO_BLEND_WEIGHT * elo_prob + (1 - ELO_BLEND_WEIGHT) * sim_prob`
`ELO_BLEND_WEIGHT = 0.50` — the simulation captures matchup-level detail (lineups, platoon splits, park factors) while Elo captures season-level team strength that the compressed simulation misses.

**Why needed:** The PA-by-PA MC simulation is structurally compressed (law of large numbers over ~35 PA/team → model std=0.057 vs market std=0.110). Elo blending helps widen the probability spread to be more realistic.

## Multiplicative Odds-Ratio Blending (v0.7)

When a specific batter faces a specific pitcher, their individual rates are combined using the multiplicative odds-ratio formula:

```
P(outcome | batter, pitcher) = b × p / l
```

Where `b` = batter rate, `p` = pitcher rate, `l` = league rate.

This is the numerator of the full log5 formula. The full log5 formula adds a denominator: `(b*p/l) + ((1-b)*(1-p)/(1-l))`. For rates far from 0.5 (HR ~3%, K ~22%, BB ~8.5%), the denominator is ≈1.0 and produces nearly identical results. For rates near 0.5 (OUT ~0.456), the full log5 denominator introduces ~7% compression. This compression compounds across 30+ PA per team into meaningful win-probability compression.

**Why multiplicative**: The simpler formula avoids the systematic compression on the OUT rate without sacrificing accuracy on the skill-based outcomes. It also preserves the correct behavior: when both batter and pitcher are league-average, the result is league-average. When one is above and the other below, the result is between them. The distribution is re-normalised after park factor adjustments, so the probabilities always sum to 1.

## Park Factors (v1.0 — BHQ)

Multiplicative adjustments applied to PA outcome rates and total runs distribution. Source: Baseball HQ park factor table (replaces FanGraphs component-only factors in v1.0).

| Feature | Source | Why included |
|---------|--------|--------------|
| **HR park factor** | BHQ (blended LHB/RHB) | Coors, CIN (+31-35% LHB HR), PNC Park (-22% RHB HR). Massive HR production variance. |
| **2B park factor** | FanGraphs 5-year regressed | Fenway's wall generates +10% more doubles |
| **3B park factor** | FanGraphs 5-year regressed | Large outfields (e.g. old Coors) produce more triples |
| **1B park factor** | BHQ (blended LHB/RHB BA) | Derived from BHQ BA factors; parks with high BA boost singles |
| **BB park factor** (v1.0) | BHQ | KC +10%, Oracle Park -14%. Walk-friendly parks produce more baserunners → more runs. Previously ignored. |
| **K park factor** (v1.0) | BHQ | Seattle +14%, KC -7%. High-K parks suppress run scoring. Previously ignored. |
| **Runs park factor** (v1.0) | BHQ | Coors +30%, Seattle -17%. Scales total_runs_dist before computing P(over)/P(under) for totals betting. Captures residual park effects (altitude, dimensions, weather) that component factors alone miss. |
| **Platoon HR factors** (v1.0) | BHQ (LHB HR, RHB HR) | Yankee Stadium +17% LHB / +16% RHB; ARI -28% LHB / -13% RHB. Stored in data, blended factor used in PA model. |
| **Platoon BA factors** (v1.0) | BHQ (LHB BA, RHB BA) | Fenway +10% LHB, Seattle -9% LHB / -14% RHB. Stored in data for future per-PA platoon application. |

## Bullpen Modelling (v0.6, tiered in v1.2)

After the starter is pulled (22 BF), a **team-specific tiered bullpen profile** takes over for the rest of the game (~3 innings).

**Implementation:**
1. `extract_team_relievers()` in `process.py` identifies relievers per game from Statcast — first pitcher per side = starter, all others = relievers
2. `aggregate_team_bullpen_rates()` filters Statcast to reliever PAs only (merged on pitcher_id + game_pk), aggregates per-pitcher rates with `min_bf=10`, then maps pitchers to teams
3. `build_tiered_bullpen_profiles()` in `pitching.py` splits relievers into **high-leverage** and **low-leverage** tiers based on quality metric (K% - BB% - 3×HR%, a FIP proxy), split at cumulative 50% of total BF. Returns (hi, lo) tuple. Minimum 2 relievers per tier; < 4 total relievers → both tiers get the same blended profile.
4. In the simulation, `_simulate_half_inning()` selects tier at the start of each half-inning based on run differential: `|run_diff| ≤ BULLPEN_HIGH_LEV_THRESHOLD` → high-leverage, else low-leverage. Extra innings always use high-leverage.
5. `monte_carlo_win_probability()` accepts optional `home_bullpen_lo` / `away_bullpen_lo` kwargs (backward compatible — if omitted, high-leverage profile used for both).

**Rolling backtest:** `CumulativeStats` tracks relievers via `register_reliever(pitcher_id, team)` as each day's games are processed. `get_team_reliever_rates()` exports per-team reliever rate DataFrames on demand.

**Config:** `BULLPEN_HIGH_LEV_THRESHOLD = 2` in `config.py`.

**Impact:** The Dodgers' elite bullpen and the Rockies' terrible bullpen are now differentiated, adding ~3 innings of team-specific data per game. Tiering separates high-leverage arms (higher K rate ~0.283) from low-leverage arms (~0.230), better reflecting actual bullpen usage patterns.

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
| Sac fly probability | **13%** (runner on 3B, any out, < 2 outs) | MLB avg ~0.33 SF/team/game ÷ ~2.5 opportunities. Fixed from 30% in v1.0 (was 4x too high). |
| Double play probability | 12% (runner on 1B, ground ball, < 2 outs) | Historical DP rates |
| Ground ball fraction of outs | 45% | League average GB/FB split |
| Error rate | **1.4%** of outs become reached-on-error (v1.0) | 2024 MLB: ~0.55 errors/team/game |
| Productive out: 2B→3B | **18%** on non-sac-fly, non-DP outs (v1.0) | Statcast BsR 2022-2024 (0.45 GB × 0.40 advance) |
| Productive out: 1B→2B | **11%** on non-sac-fly, non-DP outs (v1.0) | Statcast BsR 2022-2024 (0.45 GB × 0.25 advance) |
| Starter batter limit | **22** batters faced (v1.2) | ~7.3 innings; matches actual 2024 MLB starter avg BF |

### Stolen Bases (v1.0)

Pre-PA stolen base attempts, calibrated to 2024 MLB averages (~0.70 SB per team per game, ~78% success rate).

**Implementation** (`game_sim.py`):
- Checked before each PA when runners are on base
- **Steal of 2B**: runner on 1B, 2B empty, < 2 outs → 7% attempt rate per PA
- **Steal of 3B**: runner on 2B, 3B empty, < 2 outs → 1.5% attempt rate per PA (~20% of all SB attempts)
- Success rate: 78% baseline, adjusted by team speed: `success = 0.78 + 0.008 * (team_SPD - 100)`, clipped to 50-95%
- Caught stealing removes the runner and adds an out
- Team speed = mean BHQ SPD of lineup (default 100 for players without BHQ data)

**Constants** (`constants.py`): `SB_ATTEMPT_RATE_1B=0.07`, `SB_ATTEMPT_RATE_2B=0.015`, `SB_SUCCESS_RATE=0.78`, `SB_SPEED_FACTOR=0.008`, `LEAGUE_AVG_SPEED=100`

**Why included:** The model was underpredicting runs scored by ~0.5 runs/game. Stolen bases create extra scoring opportunities (runner in scoring position without requiring a hit) that were previously absent from the simulation.

### Wild Pitches / Passed Balls (v1.0)

Pre-PA wild pitch and passed ball events that advance runners.

**Implementation** (`game_sim.py`):
- Checked before each PA when runners are on base (after SB check)
- 0.8% probability per PA (~0.30 WP+PB per team per game)
- All runners advance one base; runner on 3B scores

**Constants** (`constants.py`): `WILD_PITCH_RATE=0.008`

**Why included:** Another source of "free" runs the model was missing. WP/PB events are independent of batter quality and contribute ~0.15 runs/team/game.

### Base Advancement Probabilities (updated v1.0)

Updated from Statcast/FanGraphs BsR data (2022-2024 average). Key changes from prior version:

| Situation | Old | New | Source |
|-----------|-----|-----|--------|
| Runner on 1B, single → 3B | 0% | **28%** | Gap hits, aggressive baserunning |
| Runner on 1B, single → 2B | 100% | **72%** | Complement of above |
| Runner on 1B, double → scores | 45% | **56%** | Statcast BsR data |
| Runner on 1B, double → 3B | 55% | **44%** | Complement of above |

**Why updated:** The old probabilities assumed runners on 1B always stopped at 2B on singles. In reality, ~28% of runners advance to 3B on singles (gap hits to the outfield, aggressive running). This was another contributor to the model underpredicting runs.

### Errors / Reached on Error (v1.0)

Errors convert outs into baserunners — a run-scoring mechanism completely absent from the model prior to v1.0.

**Implementation** (`game_sim.py`):
- Checked first in `_handle_out()` — 1.4% of outs become reached-on-error
- Batter goes to 1B, all existing runners advance one base, runner on 3B scores
- Returns `-1` for extra_outs to signal no out was recorded
- The half-inning loop skips the out increment when it sees `-1`

**Constants** (`constants.py`): `ERROR_RATE=0.014`

**Why included:** MLB teams average ~0.55 errors/game and ~0.35 unearned runs/team/game. The model had zero errors — every out was a clean out. This was the single largest contributor to the ~0.5 run/game underprediction gap. Adding errors accounts for ~0.30-0.35 extra runs/team/game.

### Productive Outs (v1.0)

On regular outs (non-sac-fly, non-DP), runners can advance on groundball outs to the right side.

**Implementation** (`game_sim.py`):
- Checked at the end of `_handle_out()` after sac fly and DP checks
- Runner on 2B → 3B: 18% probability (when 3B is empty)
- Runner on 1B → 2B: 11% probability (when 2B is empty)
- Both can trigger on the same out

**Constants** (`constants.py`): `PRODUCTIVE_OUT_2B_TO_3B=0.18`, `PRODUCTIVE_OUT_1B_TO_2B=0.11`

**Why included:** Previously, all runners stayed frozen on regular outs. In reality, groundouts to the right side frequently advance runners into scoring position. This adds ~0.10-0.15 runs/team/game.

### Sac Fly Fix (v1.0)

`SAC_FLY_PROB` reduced from 0.30 to **0.13**.

**Why:** The old value (0.30) was calibrated assuming only fly ball outs reached the sac fly code path. But in the simulation, ALL outs pass through `_handle_out()`, so the effective sac fly rate was 0.30 × all outs ≈ 1.35 sac flies/team/game — 4x higher than MLB reality (~0.33). The corrected value: ~0.33 SF/game ÷ ~2.5 opportunities/game ≈ 0.13.

## Weather Adjustments (v1.2)

Temperature and wind affect HR/XBH rates. These factors are merged into park_factors before PA probability computation.

**Signal from 2024 MLB data (2,391 games):**
- **Temperature:** <55°F: 7.89 avg runs, 85°F+: 9.54 avg runs (+1.64 run swing)
- **Wind direction:** Out 8.82, In 8.48, Cross 8.84, Dome 9.00 (+0.34 out vs in)
- **Dome/roof:** 457 games (19%), avg 9.06 runs. Detected via "Roof Closed"/"Dome" in condition field.

**Implementation** (`src/features/weather.py`):
- `compute_weather_factors(temperature, wind_speed, wind_direction, condition)` → multiplicative factors dict (keys: HR, 2B, 3B)
- `merge_weather_into_park_factors(park_factors, weather_factors)` → merged dict
- Temperature: `temp_factor = 1.0 + 0.002 * (temp - 72)`, applied to HR/2B/3B. Clamped to [0.85, 1.15].
- Wind out: `wind_hr = 1.0 + 0.008 * wind_speed`, applied to HR only. Clamped to [0.85, 1.20].
- Wind in: `wind_hr = 1.0 - 0.006 * wind_speed`, applied to HR only.
- Cross-wind and no-wind: no HR adjustment.
- **Dome games:** No weather effect (returns None, park_factors unchanged).
- **Retractable roof parks:** Conservative — treated as roof closed in daily pipeline (can't predict roof status).

**Data Sources:**
- **Backtesting:** MLB Stats API `gameData.weather` — temp, field-relative wind direction, condition. Cached in `data/cache/game_weather_{year}.pkl`.
- **Daily pipeline:** Open-Meteo forecast API (free, no key, 10K calls/day). Compass wind bearing converted to field-relative using park CF bearing table (`PARK_CF_BEARING`). Uses 7 PM local hour forecast.

**Why included:** The +0.48 run overshoot (model 9.28 vs actual 8.80) is partly from ignoring cold-weather games that suppress HR/XBH. Weather adjustments help close this gap and improve totals calibration.

## Per-Sportsbook Odds Display (v1.1)

The daily pipeline now collects and displays odds from every available sportsbook, not just the best line.

**Data flow:**
1. `parse_odds_response()` in `odds.py` collects `books_home` / `books_away` dicts with each sportsbook's American odds
2. `run_daily.py` attaches `sportsbook_odds` dict to each pick in the daily JSON
3. The website renders color-coded badges per sportsbook on each pick card

**Sportsbook color scheme:**
| Book | CSS Class | Color |
|------|-----------|-------|
| FanDuel | `.fanduel` | Blue (#1877F2) |
| DraftKings | `.draftkings` | Green (#1B8A3E) |
| BetMGM | `.betmgm` | Gold (#9E7C1F) |
| Caesars | `.williamhill_us` | Burgundy (#8E243C) |
| BetRivers | `.betrivers` | Purple (#6B3FA0) |
| ESPN BET | `.espnbet` | Red (#C93426) |
| Fanatics | `.fanatics` | Teal (#007A72) |
| Pinnacle | `.pinnacle` | Dark gray (#444950) |

The best available odds get a ring highlight (`.best-odds` class). This helps users quickly identify where to place their bet for maximum value.

## Betting Features

### Moneyline odds

- **Source**: The Odds API historical endpoint, FanDuel as single book (consensus fallback)
- **Closing line construction**: FanDuel odds for both home and away (coherent pair from one book). Filters: |American odds| ≤ 600, vig 0-12%, minimum 3 books available per game. Falls back to median-consensus when FanDuel is missing.
- **Edge detection**: `adjusted_prob = market + (alpha * confidence) * (model - market)`. ML: α=0.9, edge 7-15%, min confidence 0.5 (grid-search optimal from v0.9+BHQ).
- **Sizing**: Quarter-Kelly with 5% hard cap
- **Max edge cap**: Edges above 15% filtered out (market is right when disagreement is that large)

### Run Line (spread ±1.5)

- **Source**: The Odds API historical endpoint, spreads market. FanDuel as single book (consensus fallback). Only standard ±1.5 lines kept (alternate lines filtered out).
- **Model probability**: From MC simulation margin distribution — `P(home covers -1.5) = count(margin > 1.5) / N`. This correctly captures the home/away asymmetry: home teams win by exactly 1 run 34.3% of the time (don't bat in bottom 9th, walk-offs) vs 23.9% for away teams.
- **Edge & sizing**: Spread: α=0.9, edge 7-15%, min confidence 0.5 (same as ML). Quarter-Kelly with 5% hard cap.

### Totals (over/under)

- **Source**: Same API, totals market. Consensus line = mode across books, FanDuel odds on that line (median fallback).
- **Model probability**: From MC simulation distribution — `P(over) = count(total > line) / count(total ≠ line)` (pushes excluded)
- **Edge & sizing**: Totals: α=0.3, edge 7-15%, no min confidence (grid-search optimal from v0.9+BHQ). Quarter-Kelly with 5% hard cap.

## Probability Spread (resolved in v0.7)

The model's predicted win probabilities were historically narrower than the market's range (~20-80%). This was the #1 issue through v0.6. As of v0.7, the model's probability std (0.108) matches the market's (0.110) within 2%.

**Addressed across v0.6-v0.7:**
1. ~~Heavy regression weights~~ → Split scales: batter 0.20, pitcher 0.08 (v0.7)
2. ~~League-average bullpen~~ → Team-specific bullpen profiles (v0.6)
3. ~~No times-through-order penalty~~ → +10%/+20% hit boost for 2nd/3rd TTO (v0.6)
4. ~~Log5 double-compression~~ → Multiplicative `b*p/l` removes ~7% OUT compression (v0.7)

**Remaining cause:**
5. **Structural simulation compression** — PA-by-PA MC simulation compressed by law of large numbers (model std=0.057 before Elo). Addressed in v0.9 with Elo blending (50/50) which widens spread. Marcel + BHQ priors also improve early-season profiles. Alpha shrinkage at the betting layer compensates further.

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

### v0.8 — Confidence-gated edges + totals QC (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **Totals quality controls** | `fetch.py` | Done — per-book vig, min 3 books, |odds|≥100, final-pair validation |
| **Confidence-gated edge detection** | `edge.py` | Done — season depth + model-market agreement |
| **Grid search tool** | `scripts/analyze_edges.py` | Done — sweeps alpha/edge/conf on existing CSV |

### v0.9 — Marcel + BHQ + Elo + separate params + HFA (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **Marcel preseason projections** | `src/features/marcel.py`, `cumulative.py`, `runner.py` | Done — 3-year weighted, regression, age adjustment, platoon splits |
| **BHQ skills-based blend** | `src/data/bhq.py`, `src/features/bhq_rates.py`, `marcel.py` | Done — 50/50 blend, ~530 batters + ~650 pitchers, calibrated correlations |
| **Elo team-strength layer** | `src/features/elo.py`, `runner.py` | Done — K=20, HFA=24, regression=1/4, 50% blend with sim |
| **Separate ML/Totals params** | `config.py`, `runner.py`, `edge.py` | Done — ML α=0.9 edge 7-15% conf≥0.5, Totals α=0.3 edge 7-15% (grid-search optimal) |
| **Max edge cap** | `config.py`, `runner.py`, `edge.py` | Done — 15% cap filters out market-is-right overconfidence |
| **Home field advantage** | `runner.py` | Done — +2.5% additive boost to home win prob |
| **9.4x simulation speedup** | `game_sim.py` | Done — pre-computed PA arrays (38s→4s per game) |
| **Dashboard v2** | `dashboard/v2.py` | Done — single-page ROI chart + bet tables |
| **v0.9 rolling backtest (no BHQ)** | — | Complete — see backtest_results.md |
| **v0.9+BHQ rolling backtest** | — | Complete — ML +1.3% ROI, Totals +5.1% ROI (optimal params) |
| **2025 out-of-sample validation** | — | **Complete — ML +12.0% ROI (250 bets), $20K→$37.6K. Model validated.** |
| **Odds QC: total line range** | `fetch.py` | Done — 5.5-14.0 plausible MLB range |
| **Odds QC: ML coherence** | `fetch.py` | Done — rejects both-positive American odds pairs |

### v1.0 — BHQ Park Factors + Run Scoring Fixes (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **BHQ park factors** | `park_factors.py` | Done — replaces FanGraphs with BHQ BB/K/platoon HR/BA |
| **BB/K in PA model** | `pa_model.py` | Done — BB and K rates now park-adjusted |
| **Runs factor removed from totals** | `runner.py` | Done — was double-counting with component factors |
| **Stolen bases** | `constants.py`, `game_sim.py`, `runner.py` | Done — pre-PA SB attempts, speed-adjusted, calibrated to 2024 MLB |
| **Wild pitches / passed balls** | `constants.py`, `game_sim.py` | Done — 0.8% per PA with runners on, advances all runners |
| **Improved base advancement** | `constants.py` | Done — 1B→3B on singles (28%), 1B→score on doubles (56%) |
| **Errors / reached on error** | `constants.py`, `game_sim.py` | Done — 1.4% of outs, batter reaches 1B, runners advance |
| **Productive outs** | `constants.py`, `game_sim.py` | Done — 2B→3B (18%), 1B→2B (11%) on regular outs |
| **Sac fly fix** | `constants.py` | Done — 0.30→0.13 (was 4x overcounting) |
| **Speed scores in lineups** | `runner.py` | Done — BHQ SPD extracted and passed through lineup builder |
| **2024 backtest** | — | Complete — Brier 0.2436, ML +1.6% ROI |
| **2025 OOS validation** | — | **Complete — ML +16.2% ROI, $20K→$45.2K. Best result yet.** |

### v1.2 — Tiered Bullpen + Weather Adjustments (DONE)

| Change | Module | Status |
|--------|--------|--------|
| **Tiered bullpen** | `pitching.py`, `game_sim.py`, `runner.py`, `run_daily.py` | Done — high/low leverage tiers by quality metric, selected per half-inning by run differential |
| **Starter BF limit** | `constants.py` | Done — 21→22 BF (matches 2024 MLB avg) |
| **Weather adjustments** | `src/features/weather.py`, `runner.py`, `run_daily.py` | Done — temperature + wind factors on HR/XBH, dome detection, Open-Meteo for daily pipeline |

### v1.2+ — Additional features

| Feature | Expected impact |
|---------|-----------------|
| **Platoon-split HR in PA model** | Per-PA LHB/RHB HR adjustment (data available, not yet wired into per-PA calc) |
| **Per-player speed in base advancement** | Individual runner speed affecting advancement probs (currently team-avg for SB only) |
| **Catcher framing** | Shifts K/BB rates by ~1-2% for elite/poor framers |
| **Umpire tendencies** | Some umps have measurably larger/smaller strike zones |
| **Bullpen availability** | Back-to-back usage degrades reliever quality; bullpen games vs. traditional starts |
| **Lineup-order weighting** | Top of order gets more PA; currently all 9 cycle equally |
| **Batter vs specific pitcher history** | Only useful with 50+ PA sample (rare); mostly noise |
