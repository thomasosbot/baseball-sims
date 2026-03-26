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


def resolve_pitcher_throws(pitcher_name: str, cumulative=None) -> str:
    """
    Resolve a pitcher's throwing hand ('L' or 'R') by name.
    Resolution chain: cumulative state → statsapi lookup → default 'R'.
    """
    if not pitcher_name or pitcher_name == "TBD":
        return "R"

    # 1. Check cumulative state (reverse lookup: name → id → throws)
    if cumulative is not None:
        name_lower = pitcher_name.lower().strip()
        for pid, pname in cumulative._pitcher_names.items():
            if pname.lower().strip() == name_lower:
                throws = cumulative._pitcher_throws.get(pid)
                if throws:
                    return throws
        # Try partial match (last name)
        last_name = name_lower.split()[-1] if " " in name_lower else name_lower
        for pid, pname in cumulative._pitcher_names.items():
            if pname.lower().strip().split()[-1] == last_name:
                throws = cumulative._pitcher_throws.get(pid)
                if throws:
                    return throws

    # 2. MLB API people search (more reliable than statsapi.lookup_player)
    try:
        search_name = pitcher_name.replace(" ", "+")
        resp = _requests.get(
            f"https://statsapi.mlb.com/api/v1/people/search?names={search_name}",
            timeout=5,
        )
        if resp.ok:
            people = resp.json().get("people", [])
            if people:
                hand = people[0].get("pitchHand", {}).get("code", "R")
                return hand
    except Exception:
        pass

    return "R"


def fetch_team_platoon_lineup(
    team_id: int,
    before_date: str,
    opposing_pitcher_throws: str,
    cumulative=None,
    include_spring: bool = False,
) -> list:
    """
    Fetch a platoon-aware projected lineup for a team based on the opposing
    pitcher's handedness.  Looks at the team's recent games, filters to those
    where the opposing starter had the same hand, and builds a consensus
    batting order from those games.

    Falls back to most-recent-game lineup if insufficient platoon data.
    """
    end = datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=1)
    start = end - timedelta(days=30)
    start_str = start.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    try:
        recent = statsapi.schedule(team=team_id, start_date=start_str, end_date=end_str)
    except Exception:
        return []

    allowed = {"Final", "Game Over"}
    candidates = [g for g in recent if g.get("status") in allowed]
    if not include_spring:
        candidates = [g for g in candidates if g.get("game_type") == "R"]
    if not candidates:
        # Fall back: try including spring training
        candidates = [g for g in recent if g.get("status") in allowed]

    if not candidates:
        return []

    # Partition games by opposing pitcher handedness
    matching_games = []
    fallback_latest = candidates[-1]  # most recent game regardless

    for g in reversed(candidates):  # most recent first
        if len(matching_games) >= 5:
            break
        # Determine opposing pitcher name
        if g["home_id"] == team_id:
            opp_pitcher = g.get("away_probable_pitcher", "")
        else:
            opp_pitcher = g.get("home_probable_pitcher", "")

        throws = resolve_pitcher_throws(opp_pitcher, cumulative)
        if throws == opposing_pitcher_throws:
            matching_games.append(g)

    # If not enough matching games, fall back to last game's lineup
    if len(matching_games) < 1:
        try:
            lineups = fetch_game_lineup(fallback_latest["game_id"])
        except Exception:
            return []
        side = "home" if fallback_latest["home_id"] == team_id else "away"
        return lineups.get(side, [])

    # If only 1-2 matching games, use the most recent one
    if len(matching_games) <= 2:
        g = matching_games[0]
        try:
            lineups = fetch_game_lineup(g["game_id"])
        except Exception:
            return []
        side = "home" if g["home_id"] == team_id else "away"
        return lineups.get(side, [])

    # 3+ matching games: build consensus lineup
    # Collect lineups from matching games
    all_lineups = []
    for g in matching_games:
        try:
            lineups = fetch_game_lineup(g["game_id"])
            side = "home" if g["home_id"] == team_id else "away"
            lineup = lineups.get(side, [])
            if len(lineup) >= 9:
                all_lineups.append(lineup[:9])
            time.sleep(0.2)
        except Exception:
            continue

    if not all_lineups:
        return []

    # For each slot (0-8), find the most common player
    from collections import Counter
    consensus = []
    used_players = set()

    for slot in range(9):
        counts = Counter(lu[slot] for lu in all_lineups if slot < len(lu))
        # Pick most common player not already used
        for player_id, _ in counts.most_common():
            if player_id not in used_players:
                consensus.append(player_id)
                used_players.add(player_id)
                break

    # If we have gaps (duplicates displaced someone), fill from most-used players
    if len(consensus) < 9:
        all_players = Counter()
        for lu in all_lineups:
            for pid in lu:
                all_players[pid] += 1
        for pid, _ in all_players.most_common():
            if pid not in used_players:
                consensus.append(pid)
                used_players.add(pid)
                if len(consensus) >= 9:
                    break

    return consensus


def fetch_team_recent_lineup(team_id: int, before_date: str, include_spring: bool = False) -> list:
    """
    Fetch a team's most recent starting lineup (9 MLBAM IDs in batting order)
    from their last completed game before `before_date`.
    Returns empty list if no recent game found within 10 days.
    """
    end = datetime.strptime(before_date, "%Y-%m-%d") - timedelta(days=1)
    start = end - timedelta(days=10)
    start_str = start.strftime("%m/%d/%Y")
    end_str = end.strftime("%m/%d/%Y")

    try:
        recent = statsapi.schedule(team=team_id, start_date=start_str, end_date=end_str)
    except Exception:
        return []

    allowed = {"Final", "Game Over"}
    candidates = [g for g in recent if g.get("status") in allowed]
    if not include_spring:
        candidates = [g for g in candidates if g.get("game_type") == "R"]

    if not candidates:
        return []

    # Most recent game
    latest = candidates[-1]
    try:
        lineups = fetch_game_lineup(latest["game_id"])
    except Exception:
        return []

    # Return the lineup for the side this team was on
    if latest["home_id"] == team_id:
        return lineups.get("home", [])
    else:
        return lineups.get("away", [])


def fetch_daily_lineups(date: str, include_spring: bool = False, use_projected: bool = False, cumulative=None) -> List[dict]:
    """
    Fetch all games and their starting lineups for a given date (YYYY-MM-DD).
    Returns list of dicts, each with: game_id, home_team, away_team,
    home_lineup (list of MLBAM IDs), away_lineup, home_starter, away_starter,
    home_score, away_score, status, lineup_status.

    If use_projected=True, games with missing lineups will fall back to a
    platoon-aware projected lineup based on the opposing pitcher's handedness.
    Pass cumulative (CumulativeStats) for pitcher handedness resolution.
    """
    m, d, y = date[5:7], date[8:10], date[:4]
    games = statsapi.schedule(date=f"{m}/{d}/{y}")
    results = []
    allowed_types = {"R", "S"} if include_spring else {"R"}
    for g in games:
        if g.get("game_type") not in allowed_types:
            continue
        try:
            lineups = fetch_game_lineup(g["game_id"])
        except Exception:
            lineups = {"home": [], "away": []}

        # Determine lineup status
        home_lineup = lineups["home"]
        away_lineup = lineups["away"]
        home_projected = False
        away_projected = False

        if use_projected:
            if len(home_lineup) < 9:
                # Home team faces away pitcher — resolve handedness for platoon
                away_pitcher_name = g.get("away_probable_pitcher", "")
                away_throws = resolve_pitcher_throws(away_pitcher_name, cumulative)
                fallback = fetch_team_platoon_lineup(
                    g["home_id"], date, away_throws,
                    cumulative=cumulative, include_spring=include_spring,
                )
                if len(fallback) < 9:
                    # Final fallback: old method (most recent game)
                    fallback = fetch_team_recent_lineup(g["home_id"], date, include_spring)
                if len(fallback) >= 9:
                    home_lineup = fallback
                    home_projected = True
                    time.sleep(0.2)
            if len(away_lineup) < 9:
                # Away team faces home pitcher — resolve handedness for platoon
                home_pitcher_name = g.get("home_probable_pitcher", "")
                home_throws = resolve_pitcher_throws(home_pitcher_name, cumulative)
                fallback = fetch_team_platoon_lineup(
                    g["away_id"], date, home_throws,
                    cumulative=cumulative, include_spring=include_spring,
                )
                if len(fallback) < 9:
                    fallback = fetch_team_recent_lineup(g["away_id"], date, include_spring)
                if len(fallback) >= 9:
                    away_lineup = fallback
                    away_projected = True
                    time.sleep(0.2)

        if home_projected or away_projected:
            lineup_status = "projected"
        elif len(home_lineup) >= 9 and len(away_lineup) >= 9:
            lineup_status = "confirmed"
        else:
            lineup_status = "pending"

        results.append({
            "game_id":       g["game_id"],
            "game_date":     date,
            "home_team":     g["home_name"],
            "away_team":     g["away_name"],
            "home_id":       g["home_id"],
            "away_id":       g["away_id"],
            "venue":         g.get("venue_name", ""),
            "home_lineup":   home_lineup,
            "away_lineup":   away_lineup,
            "home_starter":  g.get("home_probable_pitcher", ""),
            "away_starter":  g.get("away_probable_pitcher", ""),
            "home_score":    g.get("home_score"),
            "away_score":    g.get("away_score"),
            "status":        g.get("status", ""),
            "lineup_status": lineup_status,
        })
        time.sleep(0.2)  # gentle rate limiting

    return results


# ---------------------------------------------------------------------------
# RotoGrinders — projected lineups (night-before / pre-confirmed)
# ---------------------------------------------------------------------------

_RG_URL = "https://rotogrinders.com/lineups/mlb"

# RotoGrinders team abbrev → our standard abbrevs
_RG_TEAM_MAP = {
    "ARI": "ARI", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
    "CHC": "CHC", "CHW": "CWS", "CIN": "CIN", "CLE": "CLE",
    "COL": "COL", "DET": "DET", "HOU": "HOU", "KC": "KCR",
    "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
    "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
    "PHI": "PHI", "PIT": "PIT", "SD": "SDP", "SF": "SFG",
    "SEA": "SEA", "STL": "STL", "TB": "TBR", "TEX": "TEX",
    "TOR": "TOR", "WSH": "WSN",
}


def fetch_rotogrinders_lineups(date: str = None) -> List[dict]:
    """
    Scrape projected lineups from RotoGrinders for a given date.

    Returns list of dicts, each with:
        away_team, home_team (standard abbrevs),
        away_pitcher, home_pitcher, away_pitcher_throws, home_pitcher_throws,
        away_lineup (list of player name strings in batting order),
        home_lineup (list of player name strings in batting order),
        away_confirmed (bool), home_confirmed (bool),
        game_time (str like "7:05 PM ET")
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        print("  WARNING: beautifulsoup4 not installed, skipping RotoGrinders")
        return []

    params = {}
    if date:
        params["date"] = date
    try:
        resp = _requests.get(_RG_URL, params=params, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        resp.raise_for_status()
    except Exception as e:
        print(f"  WARNING: RotoGrinders fetch failed: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    game_cards = soup.select("div.game-card")
    if not game_cards:
        print("  WARNING: No game cards found on RotoGrinders")
        return []

    results = []
    for card in game_cards:
        try:
            game = _parse_rg_game_card(card)
            if game:
                results.append(game)
        except Exception:
            continue

    return results


def _parse_rg_game_card(card) -> Optional[dict]:
    """Parse a single RotoGrinders game card into a dict."""
    # Team abbreviations (away first, home second)
    team_plates = card.select("span.team-nameplate-title")
    if len(team_plates) < 2:
        return None
    away_abbr_raw = team_plates[0].get("data-abbr", "")
    home_abbr_raw = team_plates[1].get("data-abbr", "")
    away_abbr = _RG_TEAM_MAP.get(away_abbr_raw, away_abbr_raw)
    home_abbr = _RG_TEAM_MAP.get(home_abbr_raw, home_abbr_raw)

    # Game time
    weather_div = card.select_one("div.game-card-weather")
    game_time = ""
    if weather_div:
        time_span = weather_div.select_one("span.small")
        if time_span:
            game_time = time_span.get_text(strip=True)

    # Lineup cards (away first, home second)
    lineup_cards = card.select("div.lineup-card")
    if len(lineup_cards) < 2:
        return None

    away_data = _parse_rg_lineup_card(lineup_cards[0])
    home_data = _parse_rg_lineup_card(lineup_cards[1])

    return {
        "away_team": away_abbr,
        "home_team": home_abbr,
        "away_pitcher": away_data["pitcher_name"],
        "home_pitcher": home_data["pitcher_name"],
        "away_pitcher_throws": away_data["pitcher_throws"],
        "home_pitcher_throws": home_data["pitcher_throws"],
        "away_lineup": away_data["batters"],
        "home_lineup": home_data["batters"],
        "away_confirmed": away_data["confirmed"],
        "home_confirmed": home_data["confirmed"],
        "game_time": game_time,
    }


def _parse_rg_lineup_card(card) -> dict:
    """Parse one side's lineup card (pitcher + batters + confirmed status)."""
    # Confirmed status
    body = card.select_one("div.lineup-card-body")
    confirmed = True
    if body and "unconfirmed" in body.get("class", []):
        confirmed = False
    # Also check for the unconfirmed banner
    if card.select_one("div.lineup-card-unconfirmed"):
        confirmed = False

    # Pitcher
    pitcher_link = card.select_one("div.lineup-card-pitcher a.player-nameplate-name")
    pitcher_name = pitcher_link.get_text(strip=True) if pitcher_link else "TBD"

    pitcher_throws = "R"
    pitcher_section = card.select_one("div.lineup-card-pitcher")
    if pitcher_section:
        stats_spans = pitcher_section.select("span.small")
        for s in stats_spans:
            txt = s.get_text(strip=True)
            if txt in ("(L)", "(R)", "(S)"):
                pitcher_throws = txt.strip("()")
                break

    # Batters in order
    batters = []
    players = card.select("li.lineup-card-player")
    for player in players:
        name_link = player.select_one("a.player-nameplate-name")
        if name_link:
            batters.append(name_link.get_text(strip=True))

    return {
        "pitcher_name": pitcher_name,
        "pitcher_throws": pitcher_throws,
        "batters": batters,
        "confirmed": confirmed,
    }


def resolve_rg_player_to_id(player_name: str, cumulative=None) -> Optional[int]:
    """
    Resolve a RotoGrinders player name to an MLBAM ID.
    Checks cumulative batter/pitcher names first, then MLB API search.
    """
    if not player_name or player_name == "TBD":
        return None

    name_lower = player_name.lower().strip()

    # Check cumulative batter names
    if cumulative is not None:
        for bid, bname in cumulative._batter_names.items():
            if bname.lower().strip() == name_lower:
                return bid
        # Partial match (last name + first initial)
        parts = name_lower.split()
        if len(parts) >= 2:
            last = parts[-1]
            first_init = parts[0][0]
            for bid, bname in cumulative._batter_names.items():
                bparts = bname.lower().strip().split()
                if len(bparts) >= 2 and bparts[-1] == last and bparts[0][0] == first_init:
                    return bid

    # MLB API search fallback
    try:
        search_name = player_name.replace(" ", "+")
        resp = _requests.get(
            f"https://statsapi.mlb.com/api/v1/people/search?names={search_name}",
            timeout=5,
        )
        if resp.ok:
            people = resp.json().get("people", [])
            if people:
                return people[0]["id"]
    except Exception:
        pass

    return None


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

        # Coherence: favorite must have more negative odds than underdog
        # (both negative = one must be more negative; one neg one pos = fine)
        home_neg = best_home_odds < 0
        away_neg = best_away_odds < 0
        if home_neg and away_neg:
            # Both negative: the bigger favorite should have more negative odds
            # This is structurally fine — just skip if both are identical (pick'em edge case)
            pass
        elif not home_neg and not away_neg:
            # Both positive — incoherent moneyline (one side must be negative)
            continue

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
MIN_TOTAL_LINE = 5.5       # MLB totals never go below 5.5
MAX_TOTAL_LINE = 14.0      # MLB totals never go above 14


def build_closing_totals(totals_df: pd.DataFrame) -> pd.DataFrame:
    """
    From raw historical totals odds, build one row per game using a **single
    book** (FanDuel) for consistency.  Falls back to median when FanDuel is
    missing.

    Quality filters (mirroring build_closing_lines):
      - |odds| >= 100 (American odds below ±100 are nonsensical)
      - Per-book vig 0-12%
      - Minimum 3 books per game
    """
    from src.betting.odds import american_to_prob, remove_vig

    # Filter garbage lines: max odds cap + minimum |odds| >= 100 + plausible total line range
    clean = totals_df[
        (totals_df["over_odds"].abs() <= MAX_TOTAL_LINE_ODDS)
        & (totals_df["under_odds"].abs() <= MAX_TOTAL_LINE_ODDS)
        & (totals_df["over_odds"].abs() >= 100)
        & (totals_df["under_odds"].abs() >= 100)
        & (totals_df["total_line"] >= MIN_TOTAL_LINE)
        & (totals_df["total_line"] <= MAX_TOTAL_LINE)
    ].copy()

    # Compute per-book implied probs and vig (same as build_closing_lines)
    clean["_o_imp"] = clean["over_odds"].apply(american_to_prob)
    clean["_u_imp"] = clean["under_odds"].apply(american_to_prob)
    clean["_vig"] = clean["_o_imp"] + clean["_u_imp"] - 1.0

    # Filter to coherent lines: vig should be 0-12%
    clean = clean[(clean["_vig"] > -0.01) & (clean["_vig"] < 0.12)]

    records = []

    for (date, home, away), gdf in clean.groupby(["game_date", "home_team", "away_team"]):
        # Minimum 3 books per game (same as moneyline filter)
        if len(gdf) < 3:
            continue

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

        # No-vig — also validate the final pair's vig (consensus medians can
        # produce impossible pairs when over/under medians come from different books)
        over_imp = american_to_prob(best_over_odds)
        under_imp = american_to_prob(best_under_odds)
        final_vig = (over_imp + under_imp) - 1.0
        if final_vig < -0.01 or final_vig > 0.12:
            continue
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
            "totals_vig":       final_vig,
            "n_books":          len(gdf),
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
