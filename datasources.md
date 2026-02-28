# Data Sources

## Data libraries

- **pybaseball** — Python wrapper for Statcast (Baseball Savant). Statcast is the primary data source.
- **MLB-StatsAPI** (statsapi) — Official MLB API for schedules, lineups, boxscores, player metadata.
- **FanGraphs** — currently broken in pybaseball 2.2.7 (403 on `/leaders-legacy.aspx`). We derive all player stats from Statcast instead.

All API results are cached to `data/cache/` as pickle files after first fetch.

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

## The Odds API

**Endpoint:** `https://api.the-odds-api.com/v4/sports/baseball_mlb/odds`

| Parameter | Value |
|-----------|-------|
| `apiKey` | From `.env` file (`ODDS_API_KEY`) |
| `regions` | `us` (DraftKings, FanDuel, BetMGM, etc.) |
| `markets` | `h2h` (moneyline), `totals` (over/under) |
| `oddsFormat` | `american` |

**Rate limits:** 500 requests/month on the free tier. The response header `x-requests-remaining` tells you how many are left. As of Feb 2026: ~5,100 requests remaining.

**Key fields in response:**
- `home_team`, `away_team`, `commence_time`
- `bookmakers[].markets[].outcomes[].price` — the American odds for each side at each book
- For totals: `bookmakers[].markets[].outcomes[].point` — the total line (e.g. 8.5)

**Sportsbooks available (~13):** FanDuel, DraftKings, BetMGM, Caesars, BetRivers, PointsBet, WynnBET, Bovada, BetUS, etc.

**Data quality notes:**
- Some books (pointsbetus, wynnbet) have garbage placeholder lines (-1000000, +20000). These are filtered out.
- Pinnacle (`bookmaker.key == "pinnacle"`) is the sharpest line but its `pinnacle_home`/`pinnacle_away` columns are often null in 2024 data. Do not rely on Pinnacle-specific columns.

### Historical Odds Endpoint

**Endpoint:** `https://api.the-odds-api.com/v4/historical/sports/baseball_mlb/odds`

| Parameter | Value |
|-----------|-------|
| `date` | ISO 8601 timestamp, e.g. `2024-07-01T22:00:00Z` |
| `markets` | `h2h,totals` (both fetched in same request) |
| Other params | Same as live endpoint (`apiKey`, `regions`, `oddsFormat`) |

**Strategy:** For each game day, we fetch the snapshot at 22:00 UTC (~6pm ET). Most MLB games start 7-8pm ET, so this captures near-closing lines. Costs ~180 API requests per full season (one per game day).

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

**Team name mapping:** The Odds API uses full names ("New York Yankees") which match our `TEAM_NAME_TO_ABBREV` dict. All-Star Game entries ("American League" / "National League") are ignored.

## Cache Files

All fetched data is stored in `data/cache/` as pickle files:

| File | Contents |
|------|----------|
| `statcast_{year}.pkl` | Full Statcast pitch-level data for the season |
| `schedule_{year}.pkl` | MLB schedule with scores |
| `historical_odds_{year}.pkl` | Raw h2h odds from all books |
| `historical_totals_{year}.pkl` | Raw totals odds from all books |
| `closing_lines_{year}.pkl` | Processed per-game closing moneylines |
| `closing_totals_{year}.pkl` | Processed per-game closing totals |

Delete any cache file to force a re-fetch on next run.

## Data Not Yet Integrated (planned)

| Source | What | Why |
|--------|------|-----|
| Steamer / ZiPS | Preseason projections | Better priors than raw prior-year Statcast for early-season rolling backtest |
| Rotowire / Baseball Press | Confirmed lineups (posted ~2-4 hours before first pitch) | More reliable than probable pitcher announcements for daily pipeline |
| Retrosheet | Historical play-by-play | Better backtest: actual lineups, pitcher changes, base-out states |
| Weather APIs | Wind speed/direction, temperature, humidity | Wind at Wrigley and Coors significantly affects HR rates |
