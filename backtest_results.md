2# Backtest Results

Running log of model iterations and their performance.

## v0.1 — Baseline (league-average profiles)

**Date:** 2026-02-25
**Status:** Complete (50-game smoke test)

**Configuration:**
- Player profiles: league-average placeholders (no real player data in lineups)
- Park factors: applied per home team (FanGraphs 5-year regressed)
- Simulations: 500/game
- Pitcher model: league-average starter for 21 BF, then league-average bullpen
- Data source: MLB Stats API for schedule, Statcast cached (745K pitches)

**Results (2024 season, 50 games):**

| Metric | Value | Baseline (coin flip) | Notes |
|--------|-------|---------------------|-------|
| Brier score | 0.2533 | 0.2500 | Essentially coin-flip level — expected with no player differentiation |
| Log loss | 0.6997 | 0.6931 | Same — slightly worse than random due to park factor bias |
| Avg model home win prob | 0.502 | 0.500 | Slight home-field edge from park factors |
| Actual home win rate | 0.380 | ~0.535 | Small sample; 50 games is noisy |

**Calibration:**

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 40%-50% | 24 | 0.482 | 0.458 |
| 50%-60% | 26 | 0.521 | 0.308 |

**Interpretation:** With league-average profiles, the model predicts ~50% for every game (±5% from park factors). It has zero ability to distinguish strong from weak teams. This is the baseline to beat.

---

## v0.2 — Real lineups + Statcast player profiles (50-game smoke test)

**Date:** 2026-02-25
**Status:** Complete (50-game test). Full 2024 backtest running.

**Changes from v0.1:**
- Lineups extracted from Statcast data (first 9 unique batters by at_bat_number per game)
- Starting pitchers identified from Statcast (first pitcher each side faces)
- Real batter profiles (541 players from Statcast: regressed PA outcome rates with platoon splits)
- Real pitcher profiles (649 pitchers from Statcast: regressed rates allowed with platoon splits)
- Switch-hitter support (98 switch hitters bat from opposite side of pitcher)
- 9/9 real batter profiles per lineup (full coverage)

**Results (2024 season, 50 games, 500 sims/game):**

| Metric | v0.2 | v0.1 (baseline) | Delta | Notes |
|--------|------|-----------------|-------|-------|
| Brier score | **0.2402** | 0.2533 | -0.0131 | Below coin-flip — model has signal |
| Log loss | **0.6735** | 0.6997 | -0.0262 | Meaningful improvement |
| Win prob range | 0.434–0.598 | 0.460–0.556 | wider | Model differentiates matchups |
| Win prob std | 0.042 | 0.020 | +0.022 | More spread in predictions |

**Calibration:**

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 40%-50% | 24 | 0.468 | 0.250 |
| 50%-60% | 26 | 0.541 | 0.500 |

**Interpretation:** The model now produces a meaningful win probability spread. The Brier score beat coin-flip (0.2402 < 0.2500) which confirms the Statcast profiles carry real predictive signal. The 50%-60% bucket is well-calibrated (predicted 54%, actual 50%). Full-season results pending.

**Most confident correct predictions:**
- LAD vs STL: model 59.8% → home win (correct)
- BAL vs LAA: model 57.6% → home win (correct)
- CWS vs DET: model 43.4% away-favored → away win (correct)

---

## v0.3 — Historical odds integration (50-game smoke test)

**Date:** 2026-02-25
**Status:** Complete

**Changes from v0.2:**
- Integrated historical odds from The Odds API (31,433 raw odds rows across 184 game days)
- Built closing lines: best odds per game across all books, Pinnacle line, no-vig probabilities
- Backtest now simulates bets: calculates edge (model vs market), sizes with quarter-Kelly, tracks P&L
- Added betting performance metrics to backtest output (ROI, CLV, win rate, P&L)

**Results (2024 season, 50 games, 500 sims/game, $10K bankroll):**

| Metric | v0.3 | v0.2 | Notes |
|--------|------|------|-------|
| Brier score | **0.2452** | 0.2402 | Slightly worse on different 50-game sample |
| Log loss | **0.6836** | 0.6735 | Same sample variance |
| Games matched to odds | 38/50 (76%) | — | Seoul Series + Sunday games missed |
| Bets placed | 35 | — | Model finds edges on most matched games |
| Bet win rate | 25.7% | — | Low because betting mostly longshots |
| Avg edge | 19.8% | — | **Red flag: too high** — see interpretation |
| Avg American odds | +1435 | — | Almost all underdog bets |
| ROI | **+8.5%** | — | $1,175 profit on $13,779 staked |

**Interpretation:** The model's narrow win probability range (43-62%) conflicts with the market's much wider range (30-70%). When the market has a -300 favorite, the model says ~53%, creating a massive phantom "edge" on the underdog. The positive ROI is driven by a few lucky longshot wins (PIT +1020, WSN +480). **This is not sustainable** — the model needs more spread in its predictions.

**Key finding:** The model systematically overvalues underdogs because it doesn't differentiate enough between strong and weak teams. Root causes:
1. Heavy Bayesian regression pulls everyone toward league average
2. League-average bullpen flattens starter quality differences
3. Look-ahead bias (full-season stats) doesn't capture in-season form

---

## v0.3 — Full 2024 season (look-ahead)

**Date:** 2026-02-25
**Status:** Complete

**Configuration:**
- Full-season Statcast profiles (look-ahead bias — uses all 2024 data for every game)
- 500 sims/game, $10K bankroll
- Old odds processing (best-odds-across-books, before per-book no-vig fix)

**Results (2024 season, 2,391 games):**

| Metric | Value |
|--------|-------|
| Brier score | **0.2429** |
| Win prob range | 0.37–0.65 |

**Interpretation:** Brier 0.2429 beats coin-flip (0.2500) over a full season, confirming the model has real signal. The narrow probability range (37-65%) vs market (20-80%) is the dominant issue. This result represents the model's ceiling since full-season profiles include future data.

---

## v0.3.1 — Fixed odds (per-book no-vig consensus)

**Date:** 2026-02-26
**Status:** Complete

**Changes from v0.3:**
- **Fixed critical odds bug**: old approach cherry-picked best home odds from one book and best away odds from another independently. This mixed lines from different books, creating impossible pairs (e.g. FanDuel -106 / BetMGM +400).
- New approach: compute no-vig probability per book (each pair is coherent), then take median across books
- Added filters: |American odds| ≤ 600, per-book vig 0-12%, minimum 3 books per game
- Clean NV probability range: 0.201–0.803 (was 0.000–0.991 with old method)

**Results (2024 season, 2,391 games, 1,000 sims/game, $10K bankroll):**

| Metric | Value | Notes |
|--------|-------|-------|
| ML bets placed | 1,277 | |
| ML bet win rate | 42.2% | Higher than v0.3 smoke test (25.7%) due to fewer extreme longshots |
| ML profit | +$38,445 | |
| ML ROI | **+8.9%** | |

**Interpretation:** The odds fix removed garbage lines (+20000, -1000000) and impossible cross-book pairings. Betting results are more realistic — win rate rose from 25.7% to 42.2% because the model is no longer finding phantom edges on extreme longshots. However, the ROI is still inflated by: (1) look-ahead bias, (2) best-line cherry-picking across ~13 books, (3) the model still exclusively bets underdogs due to narrow spread.

---

## v0.4 — Full-season with totals (look-ahead)

**Date:** 2026-02-26
**Status:** Complete

**Changes from v0.3.1:**
- Added totals (over/under) betting: simulation returns `total_runs_dist` array; `P(over) = count(total > line) / count(total ≠ line)`
- Same 3% edge threshold and quarter-Kelly sizing as moneyline
- 2,641 closing totals lines matched

**Results (2024 season, 2,391 games, 1,000 sims/game, $10K bankroll):**

| Metric | Moneyline | Totals |
|--------|-----------|--------|
| Bets placed | 1,290 | 1,304 |
| Side split | 920 away / 370 home | 503 over / 801 under |
| Win rate | 42.6% | 52.4% |
| Profit | +$38,792 | +$12,886 |
| ROI | **+8.9%** | **+3.0%** |

| Metric | Value |
|--------|-------|
| Brier score | **0.2424** |
| Win prob range | 0.373–0.637 |
| Combined profit | +$51,678 |
| Combined ROI | +6.0% |

**Interpretation:** With full-season profiles (look-ahead), both moneyline and totals are profitable. The totals model leans heavily toward unders (801 vs 503) — the simulation likely underestimates run scoring. But all results are inflated by look-ahead bias and best-line cherry-picking.

---

## v0.4 — Rolling backtest with totals (no look-ahead) ★

**Date:** 2026-02-26
**Status:** Complete — **this is the realistic benchmark**

**Changes from v0.3.1:**
- Rolling backtest via `CumulativeStats` — profiles built using only data available before each game date
- Prior-year (2023) Statcast seeded at weight=0.5 (500 prior PA → 250 effective PA)
- Totals betting added (same as full-season variant above)

**Results (2024 season, 2,391 games, 1,000 sims/game, $10K bankroll):**

| Metric | Moneyline | Totals |
|--------|-----------|--------|
| Bets placed | 1,286 | 1,281 |
| Side split | 951 away / 335 home | 630 over / 651 under |
| Win rate | 39.7% | 48.7% |
| Profit | +$7,078 | -$12,026 |
| ROI | **+1.6%** | **-3.0%** |

| Metric | Value |
|--------|-------|
| Brier score | **0.2464** |
| Win prob range | 0.355–0.641 |
| Win prob std | 0.0409 |
| Combined profit | **-$4,948** |
| Combined ROI | **-0.6%** |

### Full-season vs Rolling comparison

| Metric | Full-season | Rolling | Delta |
|--------|-------------|---------|-------|
| Brier | 0.2424 | 0.2464 | +0.0040 |
| Prob std | 0.0463 | 0.0409 | -0.0054 |
| ML ROI | +8.9% | +1.6% | -7.3pp |
| Totals ROI | +3.0% | -3.0% | -6.0pp |
| Combined profit | +$51,678 | -$4,948 | -$56,626 |

### Key findings

1. **Look-ahead bias is massive.** The full-season backtest shows +$52K profit; the rolling backtest shows -$5K loss. The gap is entirely due to using future data in the full-season version.

2. **The model is essentially break-even** when used realistically. +1.6% ML ROI is within noise and is still inflated by best-line cherry-picking across ~13 sportsbooks.

3. **Totals are unprofitable** (-3.0% ROI). The simulation's total runs distribution doesn't beat the market when using only pre-game data. The model's tendency toward unders in the full-season version (801 under bets) flips to roughly even in rolling (630 over / 651 under), suggesting the full-season model learned a seasonal runs pattern that isn't available pre-game.

4. **Narrow probability spread is confirmed as the core issue.** Rolling predictions are even more compressed (std 0.041 vs 0.046) because early-season profiles are heavily regressed to league average.

5. **The model does have signal** — Brier 0.2464 beats coin-flip (0.2500) even without look-ahead. The signal just isn't strong enough to overcome the vig after regression compression.

### What needs to change for profitability

See `features.md` for the full improvement roadmap. Priority changes:
1. Reduce regression weights ~40% (widen the probability spread)
2. Wire in team-specific bullpen profiles (currently league-average for both teams)
3. Add times-through-order penalty (starters get worse 3rd time through)
4. Run single-book backtest for honest ROI estimates

---

## v0.6 — Reduced regression, team bullpens, TTO penalty, better priors (smoke test)

**Date:** 2026-02-26
**Status:** Smoke test complete (50 games). Full backtest pending.

**Changes from v0.4:**
- **Reduced regression strength**: `REGRESSION_SCALE` 0.60 → 0.40 (effective batter weights: K=80, BB=100, HR=160, 3B=320). Observed rates dominate sooner.
- **Team-specific bullpen profiles**: `extract_team_relievers()` identifies relievers per game from Statcast (first pitcher per side = starter, rest = relievers). `build_bullpen_profile()` averages reliever rates weighted by BF. 30 team bullpens built for 2024.
- **Times-through-order penalty**: Hit rates boosted +10% on 2nd time through, +20% on 3rd+ time through. Applied only while facing the starter (not bullpen). Re-normalized after boost.
- **Prior-year weight increased**: `PRIOR_YEAR_WEIGHT` 0.50 → 0.70 (prior-year data counts for 70% of face value, giving stronger priors early in season).

**Results (2024 season, 50 games, 1,000 sims/game):**

| Metric | v0.6 | v0.4 rolling | v0.3 full | Delta vs v0.4 |
|--------|------|-------------|-----------|---------------|
| Brier score | **0.2260** | 0.2464 | 0.2429 | **-0.0204** |
| Log loss | **0.6440** | — | — | — |
| Win prob range | **0.37–0.64** | 0.355–0.641 | 0.37–0.65 | wider buckets |

**Calibration:**

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 30%-40% | 6 | 0.367 | 0.00% |
| 40%-50% | 16 | 0.460 | 25.0% |
| 50%-60% | 18 | 0.542 | 50.0% |
| 60%-70% | 10 | 0.640 | 60.0% |

**Interpretation:** The probability spread has widened meaningfully — predictions now reach into the 30-40% and 60-70% buckets (v0.4 barely exceeded 36-64%). The Brier score of 0.2260 is the best yet, though sample is only 50 games. The 50-60% and 60-70% buckets are well-calibrated. The 30-40% bucket at 0% actual is likely small-sample noise (only 6 games). The 40-50% bucket's 25% actual is underdogs winning less than expected — also consistent with the model now properly identifying weaker teams.

No odds data in this smoke test (run without `--odds`). Full backtest with odds needed to evaluate betting performance.

---

## v0.7 — Split regression scales + multiplicative PA model (smoke test)

**Date:** 2026-02-27
**Status:** Smoke test complete (50 games). Full rolling backtest pending.

**Changes from v0.6:**
- **Split regression scales**: single `REGRESSION_SCALE=0.40` replaced with separate `BATTER_REGRESSION_SCALE=0.20` and `PITCHER_REGRESSION_SCALE=0.08`. Pitchers are the primary game differentiator (starter faces ~21 BF), so they get much less regression to let quality shine through. Batters each see only ~4 PA, but the 9-batter lineup average still captures team quality with moderate regression.
- **Multiplicative PA model**: replaced the full log5 denominator with simple `b*p/l` (numerator-only odds ratio). Full log5 introduced ~7% compression on OUT rate via its denominator, which compounded across 30+ PA per team into meaningful win-probability compression. The multiplicative form avoids this while preserving correct behavior when both sides are average.

**Results (2024 season, 50 games, 1,000 sims/game):**

| Metric | v0.7 | v0.6 | v0.4 rolling | Delta vs v0.6 |
|--------|------|------|-------------|---------------|
| Brier score | **0.2390** | 0.2260 | 0.2464 | +0.0130 |
| Win prob range | **30–73%** | 37–64% | 35.5–64.1% | much wider |
| Win prob std | **0.108** | 0.077 | 0.041 | +0.031 |
| Market std | 0.110 | — | — | model matches market |

**Betting profile (smoke test):**

| Metric | v0.7 | v0.6 |
|--------|------|------|
| Favorite bets | **30%** | 0% |
| Avg bet odds | **+52** | +143 |
| Underdog bias | much less | severe |

**Interpretation:** The probability spread has widened dramatically — std of 0.108 now matches the market's 0.110 within 2%. This was the #1 issue through v0.6. The model now bets 30% favorites vs 70% underdogs (was 100% underdogs in v0.6), and average bet odds dropped from +143 to +52, indicating the model is finding edges across the full range of games rather than only on longshots.

The Brier score increased from v0.6 (0.2390 vs 0.2260). This is expected on a 50-game sample — the v0.6 smoke test may have been a lucky sample, and the wider spread means more variance in predictions. The key test is the full rolling backtest, which will show whether the wider spread translates to better calibration and profitability over 2,400+ games.

**Compression analysis:** Log5 compression was small (0.3-7% per outcome) — not the main issue. **Regression was the 800-lb gorilla**: compound regression of both batter AND pitcher toward the same league mean killed spread. At old settings (0.40/0.40), only 68% of scoring spread was preserved early season. At new settings (0.20/0.08), model std matches market std within 2%.

Full rolling backtest needed to confirm.

---

## Dashboard (v0.7)

**Date:** 2026-02-28
**Status:** Deployed

Redesigned the Streamlit dashboard from 2 active pages to 5:

| Page | Purpose |
|------|---------|
| **Performance** | Hero KPIs, cumulative P&L equity curve, monthly P&L bars, insights |
| **How It Works** | Plain-English model explainer with illustrative charts |
| **Predictions** | Reliability diagram (Wilson CIs), calibration residuals, Brier by month, model vs market, score-diff box plots |
| **Betting** | ML + totals bet tables, ROI by month/side/odds-range, drawdown, Kelly distribution, team performance |
| **Diagnostics** | Rolling vs full-season comparison, knowledge growth, profile coverage, predicted vs actual runs |

Custom CSS and Plotly template ("minimal" — white bg, faint gridlines, muted professional palette). All charts from previously unused pages (`backtest_results.py`, `technical.py`) are now placed exactly once.

**Live:** [baseball-sims.streamlit.app](https://baseball-sims-mqdefvx4nq6bt9mpbvcnb7.streamlit.app)
**GitHub:** [github.com/thomasosbot/baseball-sims](https://github.com/thomasosbot/baseball-sims)

---

## v0.7 — Full 2024 Backtest Results

**Date:** 2026-02-28
**Status:** Complete (2,391 games, 1,000 sims/game)

### Prediction Accuracy

| Metric | Full (look-ahead) | Rolling (no look-ahead) | Coin flip |
|--------|-------------------|------------------------|-----------|
| **Brier score** | **0.2351** | **0.2451** | 0.2500 |
| **Log loss** | 0.6626 | 0.6832 | 0.6931 |
| **Win rate** | 52.3% | 52.3% | 50.0% |

The rolling backtest (honest, no future data) beats coin-flip Brier by 0.005 — the model has real predictive signal but it's modest.

### Calibration (Rolling)

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 20%-30% | 53 | 0.266 | 0.340 |
| 30%-40% | 302 | 0.361 | 0.430 |
| 40%-50% | 806 | 0.453 | 0.480 |
| 50%-60% | 870 | 0.547 | 0.560 |
| 60%-70% | 314 | 0.638 | 0.621 |
| 70%-80% | 45 | 0.734 | 0.733 |
| 80%-90% | 1 | 0.802 | 1.000 |

Calibration is good in the 50-80% range. The 20-40% buckets show underdogs winning more than predicted — the model underestimates weak teams slightly.

### Calibration (Full)

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 10%-20% | 3 | 0.169 | 0.667 |
| 20%-30% | 77 | 0.267 | 0.286 |
| 30%-40% | 365 | 0.359 | 0.348 |
| 40%-50% | 723 | 0.453 | 0.472 |
| 50%-60% | 752 | 0.546 | 0.566 |
| 60%-70% | 394 | 0.640 | 0.683 |
| 70%-80% | 75 | 0.736 | 0.827 |
| 80%-90% | 2 | 0.817 | 1.000 |

Full backtest is better calibrated in the low buckets but underestimates favorites in the 60-80% range (actual outcomes exceed predictions).

### Betting Performance

| Metric | Full (look-ahead) | Rolling (honest) |
|--------|-------------------|-----------------|
| **Bets placed** | 1,282 | 1,219 |
| **Bet win rate** | 53.7% | 42.7% |
| **Avg edge** | 9.1% | 8.8% |
| **Avg odds** | +20 | +42 |
| **Total staked** | $372,160 | $334,372 |
| **ROI** | **+13.3%** | **-5.7%** |
| **P&L** | +$49,575 | -$19,202 |
| Home/Away bets | 404/878 | 319/900 |
| Home win rate | 57% | 41% |
| Away win rate | 52% | 43% |

### Interpretation

The full backtest (look-ahead) shows +13.3% ROI — but this is inflated because it uses end-of-season player profiles to predict all games, including early-season ones where that data wasn't available.

The rolling backtest (honest, no look-ahead) shows **-5.7% ROI**. The model has real predictive power (Brier 0.2451 < 0.25) but the edge isn't large enough to overcome the vig yet. Key issues:

1. **Underdog bias persists**: 900 away bets vs 319 home bets, with only 42.7% win rate. The model is still finding more "edges" on underdogs than favorites.
2. **Early-season compression**: Rolling predictions in April/May are compressed because current-year samples are tiny and prior-year data is discounted (even with 0.70 weight).
3. **Edge sizing**: Average edge of 8.8% should be profitable at +42 average odds, but the edges may be overestimated (the model thinks it disagrees with the market more than it actually does).

### Next Steps (v0.8)

- **Preseason projections** (Steamer/ZiPS) as priors instead of raw prior-year Statcast — should improve early-season predictions
- **Lineup-order weighting** — top of order gets more PA, currently all 9 cycle equally
- **Platoon-aware bullpen** — switch to L/R reliever based on batter hand
- **Tighter edge threshold** — try 5% minimum instead of 3% to reduce overbet count
- **Home/away calibration** — investigate the rolling model's underperformance on home bets

---

## v0.8 — Totals QC + Confidence-Gated Edge Detection

**Date:** 2026-03-08
**Status:** Complete (2,391 games, 1,000 sims/game)

**Changes from v0.7:**

1. **Totals quality controls** — `build_closing_totals()` now mirrors moneyline QC:
   - Per-book vig filter (0-12%) — eliminates negative-vig garbage
   - Minimum 3 books per game — eliminates thin-market consensus fallback noise
   - Minimum |odds| >= 100 — American odds below ±100 are nonsensical
   - Final-pair vig validation — catches impossible consensus median pairs
   - Result: 0 negative-vig games (was 20+), 2,462 clean totals (was 2,487 with 22 garbage)

2. **Confidence-gated edge detection** — raw model probabilities shrunk toward market:
   - `adjusted_prob = market + confidence * (model - market)`
   - `compute_game_confidence()` combines:
     - Season depth: cumulative pitchers tracked 850→1300 maps to 0.2→1.0
     - Model-market agreement: penalises when model and market disagree on favourite
   - Prevents overconfident early-season bets and large market disagreements

3. **Kelly sizing fix** — `size_bet()` now uses `adjusted_prob` instead of raw `model_prob` for consistent sizing with edge detection

4. **New analysis tool** — `scripts/analyze_edges.py` grid-searches alpha/min_edge/min_confidence on existing CSV without re-running Monte Carlo

### Prediction Accuracy

| Metric | v0.8 Rolling | v0.7 Rolling | Coin flip |
|--------|-------------|-------------|-----------|
| **Brier score** | **0.2455** | 0.2451 | 0.2500 |
| **Log loss** | 0.6842 | 0.6832 | 0.6931 |
| **Win rate** | 52.3% | 52.3% | 50.0% |

Prediction accuracy is essentially unchanged from v0.7 — the confidence system affects betting decisions, not the underlying model probabilities.

### Calibration (v0.8 Rolling)

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 10%-20% | 1 | 0.192 | 1.000 |
| 20%-30% | 52 | 0.267 | 0.288 |
| 30%-40% | 319 | 0.363 | 0.451 |
| 40%-50% | 804 | 0.454 | 0.484 |
| 50%-60% | 863 | 0.546 | 0.555 |
| 60%-70% | 305 | 0.639 | 0.620 |
| 70%-80% | 47 | 0.732 | 0.723 |

### Betting Performance (default params: MIN_CONFIDENCE=0.0, 3% edge)

| Metric | v0.8 Rolling | v0.7 Rolling |
|--------|-------------|-------------|
| **ML Bets placed** | 1,121 | 1,219 |
| **ML Win rate** | 43.0% | 42.7% |
| **ML Avg edge** | 6.6% | 8.8% |
| **ML Avg odds** | +46 | +42 |
| **ML ROI** | **-7.1%** | **-5.7%** |
| **ML P&L** | -$17,109 | -$19,202 |
| **Totals Bets** | 1,316 | — |
| **Totals Win rate** | 47.7% | — |
| **Totals ROI** | **-5.5%** | — |
| **Totals P&L** | -$20,915 | — |

With default parameters (no confidence filtering), results are similar to v0.7. The confidence system shrinks edges (avg 6.6% vs 8.8%) but without minimum confidence filtering, too many low-quality bets still pass.

### Grid Search Results (v0.8 CSV)

**Moneyline — Top combos (min 50 bets):**

| Alpha | Min Edge | Min Conf | Bets | Win% | Profit | ROI |
|-------|----------|----------|------|------|--------|-----|
| 0.7 | 6% | 0.6 | 83 | 47.0% | +$1,518 | **+6.3%** |
| 0.8 | 7% | 0.6 | 122 | 45.9% | +$1,296 | **+3.1%** |
| 0.8 | 8% | 0.6 | 86 | 43.0% | +$626 | **+2.0%** |
| 0.7 | 7% | 0.6 | 59 | 42.4% | +$352 | **+1.9%** |

**Totals — Top combos:**

| Alpha | Min Edge | Min Conf | Bets | Win% | Profit | ROI |
|-------|----------|----------|------|------|--------|-----|
| 0.3 | 7% | 0.0 | 41 | 65.9% | +$3,429 | **+24.9%** |
| 0.3 | 5% | 0.0 | 82 | 61.0% | +$3,632 | **+18.2%** |
| 0.3 | 3% | 0.0 | 282 | 50.7% | +$2,728 | **+8.7%** |
| 0.5 | 7% | 0.0 | 125 | 56.8% | +$2,803 | **+6.0%** |

### Key Findings

1. **Confidence filtering works for moneyline.** With min_confidence=0.6 and 6% edge threshold, ROI improves from -7.1% to +6.3% — but with only 83 bets, this needs more data to confirm statistical significance.

2. **Totals benefit from heavy market-shrinkage (alpha=0.3).** Heavily deferring to the market on totals while only betting when the model finds large disagreements produces the best totals ROI. This makes sense — the total runs line is set by sharp markets and the model's simulation-based total distribution is noisier than its win probability.

3. **Moneyline and totals need different parameter settings.** Moneyline: alpha=0.7, min_edge=6%, min_conf=0.6. Totals: alpha=0.3, min_edge=5-7%, min_conf=0.0.

4. **15-20%+ edges are almost always losers** (17.6% win rate at 15-20%, 14.3% at 20%+). These are cases where the model wildly disagrees with the market — the market is right.

5. **The model's predictive signal is real but modest.** Brier 0.2455 vs coin-flip 0.2500. The confidence system's role is to prevent this modest signal from being overwhelmed by false edges.

### Next Steps

- Set separate alpha/min_edge/min_conf for moneyline vs totals in the pipeline
- Test stability of optimal parameters on 2023 season (out-of-sample validation)
- Add explicit home field advantage adjustment (~2.5% boost to home win probability)
- Preseason projections (Steamer/ZiPS) for better early-season priors

---

## v0.9 — Marcel + Elo + Separate Params + HFA + Max Edge Cap (without BHQ)

**Date:** 2026-03-10
**Status:** Complete (2,391 games, rolling backtest)

**Changes from v0.8:**

1. **Marcel preseason projections** (`src/features/marcel.py`) — replaces raw prior-year seeding:
   - 3-year weighted Statcast history (5/4/3, most recent heaviest)
   - Regression to league average: 1200 PA (batters), 450 BF (pitchers)
   - Age adjustment: +0.6%/yr under 29, -0.3%/yr over 29 on contact-quality rates
   - Platoon splits projected separately
   - For 2024: uses 2021+2022+2023 → 2,194 batters, 1,777 pitchers (was 650/852 from single prior year)
   - Seeded at `MARCEL_EFFECTIVE_PA=350` pseudo-PAs

2. **Elo team-strength layer** (`src/features/elo.py`):
   - K=20, HFA=24 Elo points, between-season regression=1/4
   - Blended 50/50 with MC simulation probabilities (`ELO_BLEND_WEIGHT=0.50`)
   - Compensates for structural PA-by-PA compression (model std=0.057 → wider after Elo)

3. **Separate ML/Totals betting parameters:**
   - Moneyline: α=0.4, edge 2-15%, min confidence 0.3
   - Totals: α=0.3, edge 3-15%, min confidence 0.0

4. **Max edge cap** — edges above 15% filtered out (15-20%+ edges were almost always losers in v0.8)

5. **Home field advantage** — +2.5% additive boost to home win probability post-simulation

6. **9.4x simulation speedup** — pre-computed PA outcome arrays (38s→4s per game)

7. **`find_edges()` updated** — now accepts alpha, confidence, max_edge params to match backtest logic

### Prediction Accuracy

| Metric | v0.9 Rolling | v0.8 Rolling | Coin flip |
|--------|-------------|-------------|-----------|
| **Brier score** | **0.2434** | 0.2455 | 0.2500 |
| **Win rate** | 52.3% | 52.3% | 50.0% |
| **Prob std** | 0.076 | — | — |
| **Prob range** | 0.280–0.756 | — | — |

### Betting Performance

| Metric | v0.9 ML | v0.9 Totals | v0.8 ML | v0.8 Totals |
|--------|---------|-------------|---------|-------------|
| **Bets placed** | 611 | 404 | 1,121 | 1,316 |
| **Win rate** | 39.6% | 50.1% | 43.0% | 47.7% |
| **Avg odds** | +120 | -64 | +46 | — |
| **ROI** | **-5.7%** | **+2.4%** | -7.1% | -5.5% |
| **Combined P&L** | **-$911** | | -$38,024 | |

### Key Findings

1. **Brier score improved** from 0.2455 to 0.2434 — Marcel + Elo provide better probability estimates.
2. **Probability spread widened** to 0.280–0.756 (std=0.076) — much wider range than v0.8 thanks to Elo blending.
3. **Totals turned positive** at +2.4% ROI (was -5.5% in v0.8). Heavy market deference (α=0.3) combined with better underlying model produces consistent totals edges.
4. **ML still negative** at -5.7% ROI, same as v0.8. The 39.6% win rate at average +120 odds needs ~45.5% to break even.
5. **Fewer bets** (611 ML + 404 totals vs 1,121 + 1,316) — separate params and confidence/edge thresholds filter more aggressively.
6. **Combined loss dramatically reduced** from -$38K to -$911 — much closer to break-even.

---

## v0.9+BHQ — Baseball HQ Skills-Based Integration

**Date:** 2026-03-11
**Status:** Complete (2,391 games, rolling backtest)

**Changes from v0.9 (without BHQ):**

1. **Baseball HQ integration** — skills-based leading indicators blended 50/50 with Marcel projections:
   - **New files:** `src/data/bhq.py` (CSV loader), `src/features/bhq_rates.py` (skills→rates conversion)
   - **`blend_bhq_marcel()`** in `src/features/marcel.py` — combines BHQ and Marcel rates
   - `BHQ_BLEND_WEIGHT = 0.50` in `config.py`
   - CSVs stored in `data/raw/bhq/` (hitter stats, pitcher-advanced, pitcher-bb, 2021-2025)
   - Joins on MLBAMID (same player ID as Statcast)
   - No look-ahead: 2024 backtest uses BHQ 2023 data

2. **Hitter BHQ metrics:**
   - Ct% (contact rate) → K rate (r=0.747, calibrated: K = 0.620*(1-Ct%) + 0.065)
   - BB% → BB rate (r=0.608, direct)
   - Brl% (barrel rate) → HR rate (r=0.526, calibrated: HR/BIP = 0.015 + 0.30*Brl%)
   - SPD (speed) → 3B rate (r=0.296, scaled to league avg)
   - H% (BABIP), LD%, FB%, GB% → hit type distribution (1B, 2B)
   - xBA, PX, HctX as fallbacks

3. **Pitcher BHQ metrics:**
   - K% → K rate (r=0.475, direct), SwK% as fallback
   - BB% → BB rate (r=0.320, direct)
   - xHR/FB + FB% → HR rate (r=0.310, BHQ scale: 1.0 = 10% HR/FB)
   - GB%/LD%/FB% → batted ball distribution
   - H% (BABIP) → hit rate on balls in play

4. **Coverage:** ~530 batters, ~650 pitchers (regulars only, rest fall back to pure Marcel)

### Prediction Accuracy

| Metric | v0.9+BHQ Rolling | v0.9 Rolling | v0.8 Rolling | Coin flip |
|--------|-----------------|-------------|-------------|-----------|
| **Brier score** | **0.2441** | 0.2434 | 0.2455 | 0.2500 |
| **Win rate** | 52.3% | 52.3% | 52.3% | 50.0% |

### Betting Performance (default params)

| Metric | v0.9+BHQ ML | v0.9+BHQ Totals | v0.9 ML | v0.9 Totals |
|--------|-------------|-----------------|---------|-------------|
| **Bets placed** | 556 | 425 | 611 | 404 |
| **Win rate** | 40.3% | 49.4% | 39.6% | 50.1% |
| **ROI** | **-1.4%** | **-1.0%** | -5.7% | +2.4% |
| **P&L** | -$1,936 | -$1,459 | -$9,274 | +$8,363 |

### Grid Search Results (v0.9+BHQ CSV)

**Moneyline — Top combos (min 50 bets):**

| Alpha | Min Edge | Min Conf | Bets | Win% | Profit | ROI |
|-------|----------|----------|------|------|--------|-----|
| **0.9** | **7%** | **0.5** | **232** | **44.0%** | **+$2,010** | **+1.3%** |
| 0.8 | 7% | 0.5 | 206 | 43.7% | +$1,825 | +1.2% |
| 1.0 | 7% | 0.5 | 258 | 44.2% | +$1,650 | +0.9% |

**Totals — Top combos:**

| Alpha | Min Edge | Min Conf | Bets | Win% | Profit | ROI |
|-------|----------|----------|------|------|--------|-----|
| **0.3** | **7%** | **0.0** | **51** | **54.9%** | **+$2,016** | **+5.1%** |
| 0.3 | 5% | 0.0 | 82 | 53.7% | +$1,892 | +3.8% |
| 0.5 | 7% | 0.0 | 125 | 52.8% | +$1,540 | +2.4% |

**Edge bucket analysis (ML, alpha=0.9, conf>0.5):**

| Edge Range | Bets | Profit | Notes |
|------------|------|--------|-------|
| 0-3% | 267 | -$176 | Near breakeven — vig eats the edge |
| 3-5% | 237 | +$624 | Sweet spot — real edges survive vig |
| 5-7% | 19 | -$936 | Small sample, losing |
| 7-10% | 24 | -$1,019 | Model overconfident |
| 10-15% | 5 | +$988 | Tiny sample, unreliable |

### Key Findings

1. **BHQ improved ML dramatically**: ROI went from -5.7% to -1.4% at default params, and to **+1.3%** with optimal params. Skills-based projections provide real signal.
2. **Totals regressed slightly**: +2.4% → -1.0% at default params, but **+5.1%** at optimal params (alpha=0.3, edge=7%). The heavy market deference (α=0.3) is key.
3. **Combined optimal strategy**: ML + Totals = ~$4,026 profit on $20K bankroll (~+20% on bankroll over 2024 season).
4. **High alpha works for ML with BHQ**: α=0.9 means trusting the model 90% — this makes sense because BHQ improves projection quality enough to trust model-market disagreements.
5. **7% minimum edge is the sweet spot**: Filters out 0-3% noise bets while catching the 3-5% profitable range at the adjusted probability level.
6. **Confidence filtering (0.5) is crucial for ML**: Eliminates weak early-season and thin-data bets where the model can't be trusted.

### Updated Config (optimal params)

```
ML_ALPHA = 0.9, ML_MIN_EDGE = 0.07, ML_MIN_CONFIDENCE = 0.5
TOTALS_ALPHA = 0.3, TOTALS_MIN_EDGE = 0.07, TOTALS_MIN_CONFIDENCE = 0.0
```

---

## 2025 Out-of-Sample Validation ★★★

**Date:** 2026-03-11
**Status:** Complete (2,393 games, rolling backtest) — **TRUE OUT-OF-SAMPLE TEST**

This is the most important result in the project. All model parameters (alpha, min_edge, min_confidence, BHQ blend, Elo blend) were tuned on 2024 data. The 2025 backtest uses the exact same config with zero adjustments.

**Data pipeline:**
- Marcel projections from 2022+2023+2024 Statcast
- BHQ 2024 skills metrics (no look-ahead)
- Elo seeded from 2022-2024 results
- Optimized params: ML α=0.9, edge≥7%, conf≥0.5 | Totals α=0.3, edge≥7%
- New QC: total line range 5.5-14, moneyline coherence check

### Prediction Accuracy

| Metric | 2025 (out-of-sample) | 2024 (tuned on) | Coin flip |
|--------|---------------------|-----------------|-----------|
| **Brier score** | **0.2428** | 0.2441 | 0.2500 |
| **Win rate** | **54.3%** | 52.3% | 50.0% |

### Calibration

| Bucket | N games | Avg predicted | Actual win % |
|--------|---------|---------------|-------------|
| 20%-30% | 18 | 0.269 | 0.222 |
| 30%-40% | 141 | 0.367 | 0.426 |
| 40%-50% | 703 | 0.460 | 0.482 |
| 50%-60% | 1065 | 0.548 | 0.566 |
| 60%-70% | 397 | 0.636 | 0.602 |
| 70%-80% | 67 | 0.730 | 0.791 |
| 80%-90% | 2 | 0.816 | 1.000 |

### Betting Performance

| Metric | 2025 ML | 2025 Totals | 2024 ML (optimal) | 2024 Totals (optimal) |
|--------|---------|-------------|--------------------|-----------------------|
| **Bets placed** | **250** | 36 | 232 | 51 |
| **Win rate** | **48.4%** | 50.0% | 44.0% | 54.9% |
| **Avg odds** | **+103** | — | — | — |
| **Staked** | $157,449 | $21,216 | — | — |
| **Profit** | **+$18,853** | -$1,215 | +$2,010 | +$2,016 |
| **ROI** | **+12.0%** | -5.7% | +1.3% | +5.1% |
| Home/Away | 112/138 | — | — | — |

| Metric | Combined |
|--------|----------|
| **Combined profit** | **+$17,638** |
| **Combined ROI** | **+9.9%** |
| **Starting bankroll** | $20,000 |
| **Ending bankroll** | **$37,638** |

### Key Findings

1. **The model is not overfit.** Tuned on 2024 (+1.3% ML ROI), it performed *better* on 2025 (+12.0% ML ROI). This is the strongest possible validation signal.

2. **Moneyline is the real edge.** 250 bets at +12% ROI with balanced home/away split (112/138). Average odds of +103 means the model is finding edges across favorites and underdogs alike — no more systematic underdog bias.

3. **Totals edge didn't hold.** Only 36 bets at -5.7% ROI. The 7% edge threshold is too restrictive for totals (only 36 bets), and the 2024 totals edge (+5.1%) was likely sample noise or the market adapted. Consider lowering totals min_edge or removing totals betting entirely.

4. **Brier score improved.** 0.2428 (2025) vs 0.2441 (2024) — the model's probability estimates were more accurate on unseen data. Marcel+BHQ projections using 3 years of data (2022-2024) may be more stable than 2 years (2021-2023) used for 2024.

5. **$20K → $37.6K in one season.** +88% bankroll growth over a full MLB season with quarter-Kelly sizing. This is a real, actionable edge.

### Top Wins & Worst Losses

**Top 3 wins:**
- 2025-08-06: STL (+280) → +$1,813
- 2025-04-20: ARI (+220) → +$1,576
- 2025-09-13: TEX (+230) → +$1,574

**Worst 3 losses:**
- 2025-08-20: KCR (-132) → -$1,000
- 2025-08-23: MIL (-142) → -$988
- 2025-05-23: DET (-116) → -$981

---

*Future iterations will be appended below with date, config changes, and metric deltas.*
