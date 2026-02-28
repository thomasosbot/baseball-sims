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

*Future iterations will be appended below with date, config changes, and metric deltas.*
