"""
Data fetching layer.

Primary sources:
  - Statcast (via pybaseball)  — pitch-level data for player modelling
  - MLB Stats API (statsapi)   — schedules, lineups, game results, player metadata

FanGraphs endpoints are currently broken in pybaseball 2.2.7 (403 on leaders-legacy).
We derive all player stats directly from Statcast instead.

All results are cached to disk (pickle) to avoid redundant API calls.
"""

import time
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
import requests as _requests
import pybaseball
import statsapi

from config import CACHE_DIR, ODDS_API_KEY

pybaseball.cache.enable()


# ---------------------------------------------------------------------------
# Statcast (pitch-level data — the foundation of the model)
# ---------------------------------------------------------------------------

def fetch_statcast_season(year: int) -> pd.DataFrame:
    """
    Fetch all Statcast pitch-level data for a full MLB season.
    ~700K pitches per season.  Chunks by month to avoid parser errors
    on large single requests.
    """
    cache_path = CACHE_DIR / f"statcast_{year}.pkl"
    if cache_path.exists():
        print(f"  Loading cached Statcast data for {year}...")
        return pd.read_pickle(cache_path)

    print(f"  Fetching Statcast {year} by month...")
    months = [
        (f"{year}-03-20", f"{year}-03-31"),
        (f"{year}-04-01", f"{year}-04-30"),
        (f"{year}-05-01", f"{year}-05-31"),
        (f"{year}-06-01", f"{year}-06-30"),
        (f"{year}-07-01", f"{year}-07-31"),
        (f"{year}-08-01", f"{year}-08-31"),
        (f"{year}-09-01", f"{year}-09-30"),
        (f"{year}-10-01", f"{year}-10-31"),
    ]
    frames = []
    for start, end in months:
        try:
            chunk = pybaseball.statcast(start_dt=start, end_dt=end, verbose=False)
            frames.append(chunk)
            print(f"    {start} to {end}: {len(chunk):,} pitches")
        except Exception as e:
            print(f"    {start} to {end}: error ({e}), skipping")
        time.sleep(1)  # be polite

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    df.to_pickle(cache_path)
    print(f"  Cached {len(df):,} total pitches for {year}")
    return df


def fetch_statcast_range(start_dt: str, end_dt: str) -> pd.DataFrame:
    """Fetch Statcast data for an arbitrary date range (YYYY-MM-DD)."""
    return pybaseball.statcast(start_dt=start_dt, end_dt=end_dt, verbose=True)


def fetch_statcast_batter(batter_id: int, start_dt: str, end_dt: str) -> pd.DataFrame:
    return pybaseball.statcast_batter(start_dt, end_dt, player_id=batter_id)


def fetch_statcast_pitcher(pitcher_id: int, start_dt: str, end_dt: str) -> pd.DataFrame:
    return pybaseball.statcast_pitcher(start_dt, end_dt, player_id=pitcher_id)


# ---------------------------------------------------------------------------
# MLB Stats API — schedules, lineups, game results
# ---------------------------------------------------------------------------

def fetch_season_schedule(year: int) -> pd.DataFrame:
    """
    Fetch every regular-season game for a year via the MLB Stats API.
    Chunks by month to avoid 502 errors on large date ranges.
    Returns one row per game with home/away teams, scores, probable pitchers,
    game IDs, and venue info.
    """
    cache_path = CACHE_DIR / f"schedule_{year}.pkl"
    if cache_path.exists():
        return pd.read_pickle(cache_path)

    print(f"  Fetching {year} schedule from MLB Stats API...")
    all_games = []
    # Chunk March through October in ~2-week windows
    chunks = [
        (f"03/20/{year}", f"03/31/{year}"),
        (f"04/01/{year}", f"04/30/{year}"),
        (f"05/01/{year}", f"05/31/{year}"),
        (f"06/01/{year}", f"06/30/{year}"),
        (f"07/01/{year}", f"07/31/{year}"),
        (f"08/01/{year}", f"08/31/{year}"),
        (f"09/01/{year}", f"09/30/{year}"),
        (f"10/01/{year}", f"10/05/{year}"),
    ]
    for start, end in chunks:
        try:
            games = statsapi.schedule(start_date=start, end_date=end)
            all_games.extend(games)
            print(f"    {start}-{end}: {len(games)} games")
            time.sleep(0.3)
        except Exception as e:
            print(f"    {start}-{end}: error ({e}), retrying...")
            time.sleep(2)
            try:
                games = statsapi.schedule(start_date=start, end_date=end)
                all_games.extend(games)
            except Exception:
                print(f"    Skipping {start}-{end}")

    df = pd.DataFrame(all_games)
    if not df.empty:
        df = df[df["game_type"] == "R"].copy()
        # Deduplicate by game_id
        df = df.drop_duplicates(subset=["game_id"])
    df.to_pickle(cache_path)
    print(f"  Cached {len(df)} regular-season games")
    return df


def fetch_game_boxscore(game_id: int) -> dict:
    """
    Fetch full boxscore for a single game.
    Returns dict with 'homeBatters', 'awayBatters', 'homePitchers', 'awayPitchers',
    plus player IDs (MLBAM) that cross-reference with Statcast 'batter'/'pitcher' columns.
    """
    return statsapi.boxscore_data(game_id)


def fetch_game_lineup(game_id: int) -> dict:
    """
    Extract the starting lineup (batting order) for both teams from a boxscore.
    Returns {"home": [list of MLBAM player IDs in order], "away": [...]}.
    """
    box = statsapi.boxscore_data(game_id)
    result = {}
    for side in ("home", "away"):
        batters = box[f"{side}Batters"]
        # First entry is the header row; remaining are batters in lineup order.
        # Filter to starters (batting order 1-9), skip subs.
        lineup_ids = []
        for b in batters[1:]:
            if b.get("battingOrder", ""):
                order = str(b["battingOrder"])
                # Starters have battingOrder like "100","200",...,"900"
                # Subs have "101","201", etc.
                if order.endswith("00") and len(lineup_ids) < 9:
                    lineup_ids.append(b["personId"])
        result[side] = lineup_ids
    return result


def fetch_daily_lineups(date: str) -> List[dict]:
    """
    Fetch all games and their starting lineups for a given date (YYYY-MM-DD).
    Returns list of dicts, each with: game_id, home_team, away_team,
    home_lineup (list of MLBAM IDs), away_lineup, home_starter, away_starter,
    home_score, away_score, status.
    """
    m, d, y = date[5:7], date[8:10], date[:4]
    games = statsapi.schedule(date=f"{m}/{d}/{y}")
    results = []
    for g in games:
        if g.get("game_type") != "R":
            continue
        try:
            lineups = fetch_game_lineup(g["game_id"])
        except Exception:
            lineups = {"home": [], "away": []}

        results.append({
            "game_id":       g["game_id"],
            "game_date":     date,
            "home_team":     g["home_name"],
            "away_team":     g["away_name"],
            "home_id":       g["home_id"],
            "away_id":       g["away_id"],
            "venue":         g.get("venue_name", ""),
            "home_lineup":   lineups["home"],
            "away_lineup":   lineups["away"],
            "home_starter":  g.get("home_probable_pitcher", ""),
            "away_starter":  g.get("away_probable_pitcher", ""),
            "home_score":    g.get("home_score"),
            "away_score":    g.get("away_score"),
            "status":        g.get("status", ""),
        })
        time.sleep(0.2)  # gentle rate limiting

    return results


# ---------------------------------------------------------------------------
# The Odds API — historical odds
# ---------------------------------------------------------------------------

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"


def fetch_historical_odds_snapshot(date_iso: str, markets: str = "h2h,totals") -> dict:
    """
    Fetch a single historical odds snapshot from The Odds API.
    date_iso: ISO 8601 timestamp, e.g. '2024-07-01T22:00:00Z'
    markets: comma-separated market keys (e.g. 'h2h', 'h2h,totals')
    Returns the raw API response dict with 'data' (list of games) and timestamps.
    """
    if not ODDS_API_KEY:
        raise ValueError("ODDS_API_KEY not set in .env")

    resp = _requests.get(
        f"{_ODDS_API_BASE}/historical/sports/baseball_mlb/odds",
        params={
            "apiKey":     ODDS_API_KEY,
            "regions":    "us",
            "markets":    markets,
            "oddsFormat": "american",
            "date":       date_iso,
        },
        timeout=15,
    )
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining", "?")
    return resp.json(), remaining


def fetch_season_historical_odds(year: int, include_totals: bool = True) -> pd.DataFrame:
    """
    Fetch historical closing-line odds for every game day of a season.

    Strategy: for each game day, fetch the odds snapshot at ~18:00 ET (22:00 UTC).
    Most MLB games start between 7-8pm ET, so a 6pm snapshot is close to the
    closing line for night games and a few hours after day game starts.

    Caches the full result to disk. Costs ~180 API requests for a full season.
    When include_totals=True, also fetches over/under totals (same API call).
    """
    cache_path = CACHE_DIR / f"historical_odds_{year}.pkl"
    totals_cache = CACHE_DIR / f"historical_totals_{year}.pkl"

    # If both caches exist, just return moneyline
    if cache_path.exists() and (not include_totals or totals_cache.exists()):
        return pd.read_pickle(cache_path)

    # Get all game dates from the cached schedule
    schedule = fetch_season_schedule(year)
    game_dates = sorted(schedule["game_date"].dropna().unique())
    markets = "h2h,totals" if include_totals else "h2h"
    print(f"  Fetching historical odds ({markets}) for {len(game_dates)} game days...")

    all_rows = []
    totals_rows = []
    for i, date_str in enumerate(game_dates):
        # Normalise date to YYYY-MM-DD
        if isinstance(date_str, pd.Timestamp):
            d = date_str.strftime("%Y-%m-%d")
        else:
            d = str(date_str)[:10]

        # Snapshot at 22:00 UTC (~6pm ET)
        iso = f"{d}T22:00:00Z"
        try:
            data, remaining = fetch_historical_odds_snapshot(iso, markets=markets)
            games = data.get("data", [])

            for g in games:
                home = g["home_team"]
                away = g["away_team"]
                commence = g.get("commence_time", "")

                for bm in g.get("bookmakers", []):
                    book = bm["key"]
                    for mkt in bm.get("markets", []):
                        if mkt["key"] == "h2h":
                            home_price, away_price = None, None
                            for oc in mkt["outcomes"]:
                                if oc["name"] == home:
                                    home_price = oc["price"]
                                elif oc["name"] == away:
                                    away_price = oc["price"]

                            if home_price is not None and away_price is not None:
                                all_rows.append({
                                    "game_date":     d,
                                    "commence_time": commence,
                                    "home_team":     home,
                                    "away_team":     away,
                                    "book":          book,
                                    "home_odds":     home_price,
                                    "away_odds":     away_price,
                                })

                        elif mkt["key"] == "totals":
                            over_price, under_price, point = None, None, None
                            for oc in mkt["outcomes"]:
                                if oc["name"] == "Over":
                                    over_price = oc["price"]
                                    point = oc.get("point")
                                elif oc["name"] == "Under":
                                    under_price = oc["price"]
                                    if point is None:
                                        point = oc.get("point")

                            if over_price is not None and under_price is not None and point is not None:
                                totals_rows.append({
                                    "game_date":     d,
                                    "commence_time": commence,
                                    "home_team":     home,
                                    "away_team":     away,
                                    "book":          book,
                                    "total_line":    point,
                                    "over_odds":     over_price,
                                    "under_odds":    under_price,
                                })

            if (i + 1) % 20 == 0:
                print(f"    {i+1}/{len(game_dates)} days fetched (remaining: {remaining})")

            time.sleep(0.3)  # gentle rate limiting
        except Exception as e:
            print(f"    {d}: error ({e})")
            time.sleep(1)

    df = pd.DataFrame(all_rows)
    df.to_pickle(cache_path)
    print(f"  Cached {len(df)} h2h odds rows across {df['game_date'].nunique()} days")

    if totals_rows:
        totals_df = pd.DataFrame(totals_rows)
        totals_df.to_pickle(totals_cache)
        print(f"  Cached {len(totals_df)} totals rows across {totals_df['game_date'].nunique()} days")

    return df


MAX_AMERICAN_ODDS = 600  # Filter garbage lines — no legit MLB ML exceeds ±600


CLOSING_LINE_BOOK = "fanduel"  # Single book for consistent odds — avoids cross-book cherry-picking


def build_closing_lines(odds_df: pd.DataFrame) -> pd.DataFrame:
    """
    From raw historical odds, build one row per game using a **single book**
    (FanDuel) for both odds and no-vig probabilities.

    Using one book avoids the cross-book cherry-picking problem where
    best-home and best-away come from different books, inflating apparent
    edge on underdogs.

    Falls back to median-across-books when FanDuel is missing for a game.
    """
    from src.betting.odds import american_to_prob, remove_vig

    # Filter out garbage lines before grouping
    clean = odds_df[
        (odds_df["home_odds"].abs() <= MAX_AMERICAN_ODDS)
        & (odds_df["away_odds"].abs() <= MAX_AMERICAN_ODDS)
    ].copy()

    # Compute per-book implied probs and vig
    clean["_h_imp"] = clean["home_odds"].apply(american_to_prob)
    clean["_a_imp"] = clean["away_odds"].apply(american_to_prob)
    clean["_vig"] = clean["_h_imp"] + clean["_a_imp"] - 1.0

    # Filter to coherent lines: vig should be 0-12% for a legit moneyline
    clean = clean[(clean["_vig"] > -0.01) & (clean["_vig"] < 0.12)]

    # Compute per-book no-vig home prob
    clean["_h_nv"] = clean["_h_imp"] / (clean["_h_imp"] + clean["_a_imp"])

    records = []

    for (date, home, away), gdf in clean.groupby(["game_date", "home_team", "away_team"]):
        if len(gdf) < 3:
            continue

        # Use FanDuel if available, else fall back to median consensus
        fd = gdf[gdf["book"] == CLOSING_LINE_BOOK]
        if len(fd):
            fd_row = fd.iloc[0]
            best_home_odds = fd_row["home_odds"]
            best_away_odds = fd_row["away_odds"]
            h_nv = float(fd_row["_h_nv"])
            a_nv = 1.0 - h_nv
            h_imp = float(fd_row["_h_imp"])
            a_imp = float(fd_row["_a_imp"])
            book_used = CLOSING_LINE_BOOK
        else:
            # Fallback: median consensus
            best_home_odds = gdf["home_odds"].median()
            best_away_odds = gdf["away_odds"].median()
            h_nv = float(gdf["_h_nv"].median())
            a_nv = 1.0 - h_nv
            h_imp = float(gdf["_h_imp"].median())
            a_imp = float(gdf["_a_imp"].median())
            book_used = "consensus"

        records.append({
            "game_date":         date,
            "home_team_full":    home,
            "away_team_full":    away,
            "best_home_odds":    best_home_odds,
            "best_away_odds":    best_away_odds,
            "best_home_book":    book_used,
            "best_away_book":    book_used,
            "home_implied_prob": h_imp,
            "away_implied_prob": a_imp,
            "home_no_vig_prob":  h_nv,
            "away_no_vig_prob":  a_nv,
            "pinnacle_home":     None,
            "pinnacle_away":     None,
            "vig":               h_imp + a_imp - 1.0,
            "n_books":           len(gdf),
        })

    return pd.DataFrame(records)


MAX_TOTAL_LINE_ODDS = 300  # Filter garbage totals lines (legit lines rarely exceed ±250)


def build_closing_totals(totals_df: pd.DataFrame) -> pd.DataFrame:
    """
    From raw historical totals odds, build one row per game using a **single
    book** (FanDuel) for consistency.  Falls back to median when FanDuel is
    missing.
    """
    from src.betting.odds import american_to_prob, remove_vig

    # Filter garbage lines
    clean = totals_df[
        (totals_df["over_odds"].abs() <= MAX_TOTAL_LINE_ODDS)
        & (totals_df["under_odds"].abs() <= MAX_TOTAL_LINE_ODDS)
    ].copy()

    records = []

    for (date, home, away), gdf in clean.groupby(["game_date", "home_team", "away_team"]):
        # Use the most common total line (consensus) across books
        consensus_line = gdf["total_line"].mode().iloc[0]
        on_line = gdf[gdf["total_line"] == consensus_line]

        if on_line.empty:
            continue

        # Use FanDuel if available on consensus line, else fall back to median
        fd = on_line[on_line["book"] == CLOSING_LINE_BOOK]
        if len(fd):
            fd_row = fd.iloc[0]
            best_over_odds = fd_row["over_odds"]
            best_under_odds = fd_row["under_odds"]
            book_used = CLOSING_LINE_BOOK
        else:
            # Fallback: median odds on the consensus line
            best_over_odds = on_line["over_odds"].median()
            best_under_odds = on_line["under_odds"].median()
            book_used = "consensus"

        # No-vig
        over_imp = american_to_prob(best_over_odds)
        under_imp = american_to_prob(best_under_odds)
        over_nv, under_nv = remove_vig(over_imp, under_imp)

        records.append({
            "game_date":        date,
            "home_team_full":   home,
            "away_team_full":   away,
            "total_line":       consensus_line,
            "best_over_odds":   best_over_odds,
            "best_under_odds":  best_under_odds,
            "best_over_book":   book_used,
            "best_under_book":  book_used,
            "over_no_vig_prob": over_nv,
            "under_no_vig_prob": under_nv,
            "totals_vig":       (over_imp + under_imp) - 1.0,
        })

    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Team abbreviation mapping (MLB Stats API uses full names, Statcast uses abbrevs)
# ---------------------------------------------------------------------------

TEAM_NAME_TO_ABBREV = {
    "Arizona Diamondbacks": "ARI",  "Atlanta Braves": "ATL",
    "Baltimore Orioles": "BAL",     "Boston Red Sox": "BOS",
    "Chicago Cubs": "CHC",          "Chicago White Sox": "CWS",
    "Cincinnati Reds": "CIN",       "Cleveland Guardians": "CLE",
    "Colorado Rockies": "COL",      "Detroit Tigers": "DET",
    "Houston Astros": "HOU",        "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA",    "Los Angeles Dodgers": "LAD",
    "Miami Marlins": "MIA",         "Milwaukee Brewers": "MIL",
    "Minnesota Twins": "MIN",       "New York Mets": "NYM",
    "New York Yankees": "NYY",      "Oakland Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT",
    "San Diego Padres": "SDP",      "San Francisco Giants": "SFG",
    "Seattle Mariners": "SEA",      "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR",        "Texas Rangers": "TEX",
    "Toronto Blue Jays": "TOR",     "Washington Nationals": "WSN",
}

ABBREV_TO_TEAM_NAME = {v: k for k, v in TEAM_NAME_TO_ABBREV.items()}


def team_abbrev(full_name: str) -> str:
    """Convert 'New York Yankees' -> 'NYY'."""
    return TEAM_NAME_TO_ABBREV.get(full_name, full_name)


# ---------------------------------------------------------------------------
# Player lookup
# ---------------------------------------------------------------------------

def lookup_player(last: str, first: str) -> pd.DataFrame:
    """Look up a player's MLBAM ID via pybaseball."""
    return pybaseball.playerid_lookup(last, first)
