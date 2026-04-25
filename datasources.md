# Data Sources

## Data libraries

- **pybaseball** — Python wrapper for Statcast (Baseball Savant). Statcast is the primary data source for PA-level outcomes.
- **MLB-StatsAPI** (statsapi) — Official MLB API for schedules, lineups, boxscores, player metadata.
- **Baseball HQ (BHQ)** — Subscription-based skills metrics. CSV exports in `data/raw/bhq/` (hitter stats, pitcher-advanced, pitcher-bb). Provides leading indicators that complement Statcast counting stats.
- **FanGraphs** — currently broken in pybaseball 2.2.7 (403 on `/leaders-legacy.aspx`). We derive all player stats from Statcast instead.

All API results are cached to `data/cache/` as pickle files after first fetch. BHQ data is stored as raw CSVs (not cached/pickled).

### Statcast (via Baseball Savant)

| Function | What it returns | Rate limits | Key fields |
|----------|----------------|-------------|------------|
| `pybaseball.statcast(start_dt, end_dt)` | Every pitch thrown in the date range | ~100 pitches/second, no hard limit but large ranges are slow | `batter`, `pitcher`, `events`, `description`, `stand` (batter hand), `p_throws` (pitcher hand), `launch_speed`, `launch_angle`, `estimated_woba_using_speedangle`, `barrel`, `bb_type`, `home_team`, `away_team`, `game_date` |
| `pybaseball.statcast_batter(start_dt, end_dt, player_id)` | Single batter's pitches | Same as above | Same fields, filtered to one batter |
| `pybaseball.statcast_pitcher(start_dt, end_dt, player_id)` | Single pitcher's pitches | Same as above | Same fields, filtered to one pitcher |

**Notes:**
- Full-season Statcast pulls are ~4 GB and take 5-10 minutes on first fetch.
- `events` column is `NaN` for non-PA-ending pitches (balls, strikes, fouls). Filter to `events.notna()` for PA-level analysis.
- `estimated_woba_using_speedangle` = xwOBA at the pitch level (only populated for batted balls).

### FanGraphs (currently broken)

pybaseball 2.2.7 hits a 403 error on FanGraphs' `/leaders-legacy.aspx` endpoint. FanGraphs changed their site structure and pybaseball hasn't been updated. We derive all player stats from Statcast instead, which is actually more granular.

### MLB Stats API (statsapi)

| Function | What it returns | Rate limits | Key fields |
|----------|----------------|-------------|------------|
| `statsapi.schedule(start_date, end_date)` | All games in date range | No hard limit; chunk by month to avoid 502s | `game_id`, `home_name`, `away_name`, `home_score`, `away_score`, `home_probable_pitcher`, `away_probable_pitcher`, `status`, `venue_name` |
| `statsapi.boxscore_data(game_id)` | Full boxscore with lineups | No hard limit; add 0.2s sleep between calls | `homeBatters`, `awayBatters` (each entry has `personId`, `battingOrder`, `namefield`), `homePitchers`, `awayPitchers` |

**Notes:**
- `personId` = MLBAM ID, which matches Statcast `batter`/`pitcher` columns (the key for cross-referencing).
- `battingOrder` ending in `00` (e.g. `100`, `200`) = starter. Subs have `01`, `02`, etc.
- Schedule must be fetched in monthly chunks; full-season requests cause 502 errors.
- Team names are full strings ("New York Yankees"), mapped to abbreviations via `TEAM_NAME_TO_ABBREV` in `fetch.py`.

### Player ID Lookup

| Function | Purpose |
|----------|---------|
| `pybaseball.playerid_lookup(last, first)` | Returns MLBAM ID, FanGraphs ID, BBRef ID for cross-referencing |

## Baseball HQ (BHQ)

**Source:** Baseball HQ subscription (baseballhq.com). CSV exports stored in `data/raw/bhq/`.

### File Types

| File pattern | Contents | Years available |
|-------------|----------|-----------------|
| `mlb_seasonal_hitter_stats_and_splits-{year}.csv` | Hitter skills metrics with platoon splits | 2021-2025 |
| `mlb_seasonal_pitcher_stats-advanced-{year}.csv` | Pitcher advanced metrics (K%, BB%, SwK%, xHR/FB, etc.) | 2021-2025 |
| `mlb_seasonal_pitcher_stats-bb-{year}.csv` | Pitcher batted ball profile (GB%, LD%, FB%, H%, etc.) | 2021-2025 |

### Key ID

**MLBAMID** — the MLB Advanced Media player ID. This is the same ID used in Statcast's `batter`/`pitcher` columns and the MLB Stats API's `personId`. No ID mapping is required.

### Hitter Metrics Used

| BHQ Metric | Maps to | Correlation | Calibration |
|------------|---------|-------------|-------------|
| **Ct%** (contact rate) | K rate | r=0.747 | `K = 0.620 * (1 - Ct%) + 0.065` |
| **BB%** | BB rate | r=0.608 | Direct (BB% / 100) |
| **Brl%** (barrel rate) | HR rate | r=0.526 | `HR/BIP = 0.015 + 0.30 * Brl%` |
| **SPD** (speed score) | 3B rate + stolen base model | r=0.296 | Scaled to league average for 3B; team avg SPD adjusts SB attempt/success rates in game sim |
| **H%** (BABIP) | Hit rate on BIP | — | Used for 1B/2B distribution |
| **LD%**, **FB%**, **GB%** | Hit type distribution | — | 1B/2B split from batted ball profile |
| **xBA**, **PX**, **HctX** | Fallback indicators | — | Used when primary metrics are missing |

### Pitcher Metrics Used

| BHQ Metric | Maps to | Correlation | Calibration |
|------------|---------|-------------|-------------|
| **K%** | K rate | r=0.475 | Direct (K% / 100) |
| **SwK%** | K rate (fallback) | — | Used when K% is missing |
| **BB%** | BB rate | r=0.320 | Direct (BB% / 100) |
| **xHR/FB** + **FB%** | HR rate | r=0.310 | BHQ scale: 1.0 = 10% HR/FB |
| **GB%**, **LD%**, **FB%** | Batted ball distribution | — | Determines 1B/2B/3B split |
| **H%** (BABIP) | Hit rate on BIP | — | Direct |

### Coverage

- **Hitters:** ~530 per year (regulars with enough PA for BHQ to publish)
- **Pitchers:** ~650 per year (starters + high-usage relievers)
- Players without BHQ data fall back to pure Marcel projections

### Blending with Marcel

`src/features/marcel.py:blend_bhq_marcel()` combines BHQ skills-based rates with Marcel statistical projections:

```
blended_rate = BHQ_BLEND_WEIGHT * bhq_rate + (1 - BHQ_BLEND_WEIGHT) * marcel_rate
```

`BHQ_BLEND_WEIGHT = 0.50` in `config.py` (50% BHQ skills, 50% Marcel counting stats).

**No look-ahead:** When backtesting 2024, BHQ 2023 data is used. BHQ metrics are prior full-season summaries, not in-season updates.

### Loader

`src/data/bhq.py` reads CSV files by year, standardises column names, and returns DataFrames indexed by MLBAMID. `src/features/bhq_rates.py` converts raw BHQ metrics into the 8 PA outcome rates (K, BB, HBP, HR, 3B, 2B, 1B, OUT) used by the simulation.

### BHQ Park Factors (v1.0)

**File:** `data/raw/bhq/park factors.csv`

BHQ park factor table provides richer park adjustments than FanGraphs component-only factors:

| Factor | Description | Example values |
|--------|-------------|----------------|
| **RUNS** | Overall run environment | Coors +30%, Seattle -17%, CIN +5% |
| **LHB BA** | Batting average for left-handed batters | Coors +14%, Fenway +10%, Seattle -9% |
| **RHB BA** | Batting average for right-handed batters | Coors +17%, Seattle -14% |
| **LHB HR** | Home runs for left-handed batters | CIN +35%, PHI +29%, ARI -28% |
| **RHB HR** | Home runs for right-handed batters | LAD +35%, BAL +22%, CLE -21% |
| **BB** | Walk rate | KC +10%, MIA +6%, BAL -8%, Oracle -14% |
| **K** | Strikeout rate | Seattle +14%, MIL +14%, Coors -11% |

Empty cells = neutral (1.00). Loaded in `src/features/park_factors.py`. BB and K factors are applied in the PA model (`pa_model.py`). Runs factor scales the total_runs_dist for totals betting (`runner.py`).

## The Odds API

**Endpoint:** `https://api.the-odds-api.com/v4/sports/baseball_mlb/odds`

| Parameter | Value |
|-----------|-------|
| `apiKey` | From `.env` file (`ODDS_API_KEY`) |
| `regions` | `us` (DraftKings, FanDuel, BetMGM, etc.) |
| `markets` | `h2h` (moneyline), `totals` (over/under), `spreads` (run line ±1.5) |
| `oddsFormat` | `american` |

**Rate limits:** 500 requests/month on the free tier. The response header `x-requests-remaining` tells you how many are left. Check current balance before large fetches.

**Key fields in response:**
- `home_team`, `away_team`, `commence_time`
- `bookmakers[].markets[].outcomes[].price` — the American odds for each side at each book
- For totals: `bookmakers[].markets[].outcomes[].point` — the total line (e.g. 8.5)
- For spreads: `bookmakers[].markets[].outcomes[].point` — the spread (e.g. -1.5 / +1.5)

**Sportsbooks available (~13):** FanDuel, DraftKings, BetMGM, Caesars, BetRivers, PointsBet, WynnBET, Bovada, BetUS, ESPN BET, Fanatics, Pinnacle, etc.

**Per-sportsbook odds in daily pipeline:** `parse_odds_response()` now collects `books_home` / `books_away` dicts with every sportsbook's American odds per game. `run_daily.py` attaches `sportsbook_odds` to each pick in the daily JSON output. The website renders these as color-coded badges per book (FanDuel=blue, DraftKings=green, BetMGM=gold, Caesars=burgundy, BetRivers=purple, ESPN BET=red, Fanatics=teal, Pinnacle=dark gray), with the best odds highlighted.

## Anthropic API (Claude Opus — narrative enrichment)

**Service:** [api.anthropic.com](https://api.anthropic.com) — Claude Opus 4.7 used to draft pick narratives, post-game recaps, and the daily newsletter opener.

| Component | Details |
|-----------|---------|
| **Module** | `src/betting/narrative.py` |
| **Model** | `claude-opus-4-7` (snark voice via `SYSTEM_PROMPT_SNARK`) |
| **Functions** | `generate_narrative()` (5-6 sentence pick brief), `generate_pick_recap()` (2-3 sentence post-game), `generate_day_story()` (4-6 sentence newsletter opener) |
| **Inputs** | Structured brief built from `src/features/statcast_summary.py` rollups (xwOBA / barrel% / K% / hard-hit% with handedness splits) plus matchup metadata, edge math, weather, and play-by-play highlights |
| **Cost** | ~$0.15/day, ~$27/full season |
| **Auth** | `ANTHROPIC_API_KEY` env var (also a GitHub Actions secret for CI) |
| **Failure mode** | Every call wrapped in try/except; on any failure (missing key, API hiccup, rate limit) returns the existing rule-based explanation. Pipeline is unchanged if the LLM is unavailable. |

**Statcast rollup file:** Rollups are built from raw 2025 Statcast pitch-level data and persisted to `data/processed/statcast_rollup_2025.pkl` (~520 KB, committed to repo). The committed pickle holds 1,248 hitters and 723 pitchers with handedness splits. CI loads from this file; the multi-GB raw Statcast cache lives only on the local box. To regenerate after fetching new Statcast: `python -c "from src.features.statcast_summary import save_rollup; save_rollup(2025)"`.

**Name resolution cache:** `data/processed/name_to_mlbam.json` (committed) maps lineup names to MLBAM IDs via MLB Stats API search.

## Resend (Email Newsletter)

**Service:** [resend.com](https://resend.com) — transactional email API.

| Component | Details |
|-----------|---------|
| **Sending domain** | `ozzyanalytics.com` (verified, DNS records in Netlify) |
| **From address** | `picks@ozzyanalytics.com` |
| **Subscriber storage** | Resend Contacts API (audience-based, not local JSON) |
| **Audience ID** | Stored in `RESEND_AUDIENCE_ID` env var |
| **Subscribe flow** | Website form → Netlify function → Resend Contacts API |
| **Send flow** | `sender.py` fetches audience contacts → sends HTML email per subscriber |
| **Free tier** | 3,000 emails/month, 100/day |

**Data quality notes:**
- Some books (pointsbetus, wynnbet) have garbage placeholder lines (-1000000, +20000). These are filtered out.
- Pinnacle (`bookmaker.key == "pinnacle"`) is the sharpest line but its `pinnacle_home`/`pinnacle_away` columns are often null in 2024 data. Do not rely on Pinnacle-specific columns.

### Historical Odds Endpoint

**Endpoint:** `https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds`

| Parameter | Value |
|-----------|-------|
| `date` | ISO 8601 timestamp, e.g. `2024-07-01T22:00:00Z` |
| `markets` | `h2h,totals` or `spreads` (fetched separately to avoid re-fetching cached data) |
| Other params | Same as live endpoint (`apiKey`, `regions`, `oddsFormat`) |

**Strategy:** For each game day, we fetch the snapshot at 22:00 UTC (~6pm ET). Most MLB games start 7-8pm ET, so this captures near-closing lines. Costs ~180 API requests per full season per market (one per game day).

### Processing Pipeline — Moneyline (h2h)

1. `fetch_season_historical_odds(year, include_totals=True)` → raw odds by book per game per day → cached as `historical_odds_{year}.pkl`
2. `build_closing_lines(odds_df)` → per-book no-vig consensus → cached as `closing_lines_{year}.pkl`

**Closing line construction (critical — see `fetch.py:build_closing_lines`):**
- For each game, filter to clean lines: |American odds| ≤ 600, per-book vig 0-12%, minimum 3 books per game
- **Use FanDuel as the single book** (`CLOSING_LINE_BOOK = "fanduel"`): both home and away odds come from the same book, guaranteeing a coherent pair
- Fall back to median-consensus across books when FanDuel is missing for a game
- Compute no-vig probability from the single book's pair: `h_nv = h_imp / (h_imp + a_imp)`

**Why single-book:** The original approach selected `max(home_odds)` across all books and `max(away_odds)` independently. This mixed e.g. FanDuel's -106 home with BetMGM's +400 away — an impossible pairing. The per-book median consensus (v0.5) fixed this but still cherry-picked best odds per side for bet simulation. The single-book approach (v0.6) gives honest, realistic odds — the same pair you'd actually see at one sportsbook.

**2024 data stats:** 31,433 raw h2h odds rows → 2,413 games with clean closing lines. NV probability range: 0.201–0.803. Zero impossible odds pairs.

### Processing Pipeline — Totals (over/under)

1. Same API call as h2h (both markets fetched together)
2. Totals parsed separately → cached as `historical_totals_{year}.pkl`
3. `build_closing_totals(totals_df)` → consensus line + best odds → cached as `closing_totals_{year}.pkl`

**Totals closing line construction:**
- Consensus line = mode (most common total line across books, e.g. 8.5)
- Filter: |American odds| ≤ 300 for totals (tighter than h2h because totals odds are typically -105 to -120)
- **Use FanDuel** on the consensus line if available, else median odds across books
- No-vig probability computed from the single book's over/under pair

**2024 data stats:** 30,976 raw totals rows → 2,641 games with closing totals.

### Processing Pipeline — Spreads (run line)

1. `fetch_spread_odds(year)` in `scripts/analyze_run_lines.py` → raw spread odds by book per game per day → cached as `historical_spreads_{year}.pkl`
2. `build_closing_spreads(spreads_df)` → consensus spread + best odds → cached as `closing_spreads_{year}.pkl`

**Spread closing line construction:**
- Only standard ±1.5 run lines are kept (alternate lines like ±2.5 are filtered out)
- Filter: |American odds| ≤ 600, per-book vig 0-12%, minimum 3 books per game
- **Use FanDuel** if available, else median odds across books
- No-vig probability computed from the single book's home/away pair

**2025 data stats:** 25,125 raw spread rows → 2,116 games with closing spreads.

**Team name mapping:** The Odds API uses full names ("New York Yankees") which match our `TEAM_NAME_TO_ABBREV` dict. All-Star Game entries ("American League" / "National League") are ignored.

## Cache Files

All fetched data is stored in `data/cache/` as pickle files:

| File | Contents |
|------|----------|
| `game_weather_{year}.pkl` | Per-game weather data from MLB Stats API (temp, wind, condition) |
| `statcast_{year}.pkl` | Full Statcast pitch-level data for the season |
| `schedule_{year}.pkl` | MLB schedule with scores |
| `historical_odds_{year}.pkl` | Raw h2h odds from all books |
| `historical_totals_{year}.pkl` | Raw totals odds from all books |
| `closing_lines_{year}.pkl` | Processed per-game closing moneylines |
| `closing_totals_{year}.pkl` | Processed per-game closing totals |

**BHQ data** is stored as raw CSVs in `data/raw/bhq/` (not pickled). See the Baseball HQ section above for file patterns.

Delete any cache file to force a re-fetch on next run.

### Marcel Projections (built in-house)

Instead of relying on external projection systems (Steamer/ZiPS behind FanGraphs paywall + Cloudflare), we implemented Marcel (Tom Tango's open formula) using our own cached Statcast data.

| Parameter | Value |
|-----------|-------|
| Input | 3 prior years of Statcast (e.g., 2021+2022+2023 for 2024 projection) |
| Year weights | 5/4/3 (most recent heaviest) |
| Batter regression | 1200 PA denominator |
| Pitcher regression | 450 BF denominator |
| Age adjustment | +0.6%/yr under 29, -0.3%/yr over 29 (contact-quality rates only) |
| Output | Per-player projected PA outcome rates (8 categories) with platoon splits |

**Cached Statcast years:** 2021 (732M), 2022 (748M), 2023 (588M), 2024 (716M).

For the 2024 backtest, Marcel produces 2,194 batter and 1,777 pitcher projections from 2021-2023 data. Degrades gracefully with fewer years available.

## Weather Data (v1.2)

### MLB Stats API — Historical Game Weather

Used in backtesting. The MLB Stats API includes game-time weather in the `gameData.weather` object:

| Field | Description | Example values |
|-------|-------------|----------------|
| `temp` | Temperature (°F) | 72 |
| `wind` | Wind speed and direction (field-relative) | "9 mph, In From LF", "12 mph, Out To CF" |
| `condition` | General conditions | "Partly Cloudy", "Roof Closed", "Dome" |

**Cached as:** `data/cache/game_weather_{year}.pkl` — one row per game with columns: `game_id`, `date`, `home_team`, `away_team`, `temperature`, `wind_speed`, `wind_direction`, `wind_raw`, `condition`, `venue_name`, `home_score`, `away_score`, `total_runs`.

**Key insight:** Wind direction is already field-relative ("Out To CF" means blowing toward center field), so no park orientation conversion is needed for historical data.

**2024 stats:** 2,391 games, 457 dome/roof-closed (19%), avg temp 73.5°F, wind directions: None (428), L To R (317), Out To CF (296), R To L (282), Out To LF (273), In From LF (173), etc.

### Open-Meteo — Daily Weather Forecast

Used in the daily pipeline for game-day weather forecasts. Free, no API key required.

| Parameter | Value |
|-----------|-------|
| **Endpoint** | `https://api.open-meteo.com/v1/forecast` |
| **Rate limit** | 10,000 calls/day (free tier) |
| **Variables** | `temperature_2m`, `wind_speed_10m`, `wind_direction_10m` (hourly) |
| **Units** | Temperature in °F (`temperature_unit=fahrenheit`), wind in mph (`wind_speed_unit=mph`) |
| **Time** | Uses 7 PM local time (index 19) as typical first pitch |

**Park coordinates:** Latitude/longitude for all 30 parks stored in `PARK_COORDS` dict in `run_daily.py`.

**Wind conversion:** Open-Meteo returns compass wind bearing (degrees from north). Converted to field-relative direction using `compass_to_field_relative()` in `weather.py`, which uses `PARK_CF_BEARING` — the approximate compass bearing from home plate to center field for each park.

**Retractable roof parks:** Conservatively treated as "Roof Closed" in daily pipeline (can't predict roof status). These are: ARI, TEX, MIL, HOU, MIA, TOR, SEA.

## Derived Constants (calibrated from public data)

| Constant | Value | Source |
|----------|-------|--------|
| `SB_ATTEMPT_RATE_1B` | 0.07 | 2024 MLB: ~0.70 SB/team/game, ~13 PA with runner on 1B |
| `SB_ATTEMPT_RATE_2B` | 0.015 | ~20% of all SB attempts are steal of 3B |
| `SB_SUCCESS_RATE` | 0.78 | 2024 MLB stolen base success rate |
| `SB_SPEED_FACTOR` | 0.008 | Success rate adjustment per BHQ SPD point above/below 100 |
| `WILD_PITCH_RATE` | 0.008 | 2024 MLB: ~0.30 WP+PB/team/game, ~38 PA/team |
| `ERROR_RATE` | 0.014 | 2024 MLB: ~0.55 errors/team/game, ~38 PA/team |
| `PRODUCTIVE_OUT_2B_TO_3B` | 0.18 | Statcast BsR 2022-2024: 0.45 GB fraction × 0.40 advance rate |
| `PRODUCTIVE_OUT_1B_TO_2B` | 0.11 | Statcast BsR 2022-2024: 0.45 GB fraction × 0.25 advance rate |
| `SAC_FLY_PROB` | 0.13 | MLB ~0.33 SF/team/game ÷ ~2.5 opportunities (fixed from 0.30) |
| Base advancement (1B, runner on 1B) | 72% 2B / 28% 3B | Statcast/FanGraphs BsR, 2022-2024 avg |
| Base advancement (2B, runner on 1B) | 44% 3B / 56% score | Statcast/FanGraphs BsR, 2022-2024 avg |

## Output Channels

| Channel | Module | Status | Notes |
|---------|--------|--------|-------|
| **Website** | `site/generate.py` | Live | `ozzyanalytics.com` via Netlify. Compact stats-first homepage with announcement banner linking to /30-days.html, full-width pick rows with colored sportsbook badges + Analysis toggle showing LLM narrative, card grid for all games (ML + RL odds), individual game preview pages (`/games/YYYY-MM-DD/`) for SEO, results with P&L chart, backtest, simulator. Hidden 30-day check-in flyer at `/30-days.html` (noindex, not linked from nav). Twitter + Discord links in footer. |
| **Newsletter** | `src/newsletter/sender.py` | Live | Daily email via Resend API from `picks@ozzyanalytics.com`. Rich per-pick context (weather, pitchers, Elo, run projections) plus LLM-generated narrative per pick. Yesterday's recap with boxscore batting lines and LLM day-story opener. Retry logic for Resend rate limits. |
| **Twitter/X** | `src/twitter/poster.py`, `results_poster.py` | Live | @Ozzy_Analytics (verified). Morning: pick card image + tweet with wager amounts. Nightly: results card image + tweet with bankroll growth ($10K → current). Milestone tweets (e.g. 30-day check-in) sent ad-hoc via tweepy directly. Via tweepy (v1.1 media + v2 tweet). |
| **Discord** | `src/discord/poster.py` | Live | Ozzy Analytics server (discord.gg/mZPRnH44). Morning picks embed (with per-pick narrative) → `#daily-picks`. Nightly results embed → `#results`. Milestone embeds sent ad-hoc via webhook. Via webhooks. |
| **Reddit** | `src/reddit/poster.py` | Awaiting API approval | u/ozzy_analytics. Code ready to comment on r/sportsbook daily threads + post to own subreddit via PRAW. API application submitted. |
| **TikTok** | `src/tiktok/video.py` | Paused | Video generator works (Pillow + MoviePy, 1080x1920) but TikTok community guidelines flag betting content. Code retained. |

## Data Not Yet Integrated (planned)

| Source | What | Why |
|--------|------|-----|
| Rotowire / Baseball Press | Confirmed lineups (posted ~2-4 hours before first pitch) | More reliable than probable pitcher announcements for daily pipeline |
| Retrosheet | Historical play-by-play | Better backtest: actual lineups, pitcher changes, base-out states |
| ~~Weather APIs~~ | ~~Wind speed/direction, temperature, humidity~~ | **INTEGRATED in v1.2** — MLB Stats API for backtest, Open-Meteo for daily pipeline |
