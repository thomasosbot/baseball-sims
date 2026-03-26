"""
Odds fetching (The Odds API) and conversion utilities.
Free tier: 500 requests/month — be conservative with calls.
"""

import requests
import pandas as pd
from typing import Dict, List, Optional

from config import ODDS_API_KEY

ODDS_API_BASE  = "https://api.the-odds-api.com/v4"
MLB_SPORT_KEY  = "baseball_mlb"


# ---------------------------------------------------------------------------
# Odds math
# ---------------------------------------------------------------------------

def american_to_prob(odds: float) -> float:
    """American odds -> implied probability (includes vig)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def prob_to_american(prob: float) -> float:
    """Probability -> American odds."""
    if prob >= 0.5:
        return -(prob / (1 - prob)) * 100
    return ((1 - prob) / prob) * 100


def american_to_decimal(odds: float) -> float:
    """American odds -> decimal odds (what you get back per $1 wagered)."""
    if odds > 0:
        return (odds / 100.0) + 1
    return (100.0 / abs(odds)) + 1


def remove_vig(home_prob: float, away_prob: float) -> tuple:
    """Remove bookmaker vig by normalising implied probabilities."""
    total = home_prob + away_prob
    return home_prob / total, away_prob / total


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def fetch_mlb_odds(
    markets: str = "h2h",
    regions: str = "us",
    odds_format: str = "american",
    sport_key: str = None,
) -> List[Dict]:
    """
    Fetch current MLB moneyline odds from The Odds API.
    markets: "h2h" (moneyline) | "spreads" (runline) | "totals" (over/under)
    sport_key: override sport (e.g. "baseball_mlb_preseason" for spring training)
    """
    if not ODDS_API_KEY:
        raise ValueError(
            "ODDS_API_KEY not set.  Get a free key at https://the-odds-api.com "
            "and add it to your .env file."
        )

    sport = sport_key or MLB_SPORT_KEY
    resp = requests.get(
        f"{ODDS_API_BASE}/sports/{sport}/odds",
        params={
            "apiKey":     ODDS_API_KEY,
            "regions":    regions,
            "markets":    markets,
            "oddsFormat": odds_format,
        },
        timeout=10,
    )
    resp.raise_for_status()

    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"Odds API requests remaining this month: {remaining}")
    return resp.json()


# Only include odds from these books
ALLOWED_BOOKS = {"fanduel", "bovada", "betmgm", "draftkings", "williamhill_us"}


def parse_odds_response(odds_data: List[Dict]) -> pd.DataFrame:
    """
    Flatten The Odds API response into one row per game with:
    - best available odds for each side (across allowed books only)
    - Pinnacle line (sharpest / closest to true probability)
    - vig
    """
    rows = []
    for game in odds_data:
        home = game["home_team"]
        away = game["away_team"]

        best_home_odds, best_away_odds = None, None
        best_home_book, best_away_book = None, None
        pin_home, pin_away = None, None
        books_home, books_away = {}, {}

        for bm in game.get("bookmakers", []):
            key = bm["key"]
            # Skip books not in our allowed list (except pinnacle for sharp line)
            is_allowed = key in ALLOWED_BOOKS
            is_pinnacle = key == "pinnacle"
            if not is_allowed and not is_pinnacle:
                continue
            for mkt in bm.get("markets", []):
                if mkt["key"] != "h2h":
                    continue
                for oc in mkt["outcomes"]:
                    price = oc["price"]
                    if oc["name"] == home:
                        if is_allowed:
                            books_home[key] = price
                        if is_allowed and (best_home_odds is None or price > best_home_odds):
                            best_home_odds, best_home_book = price, key
                        if is_pinnacle:
                            pin_home = price
                    elif oc["name"] == away:
                        if is_allowed:
                            books_away[key] = price
                        if is_allowed and (best_away_odds is None or price > best_away_odds):
                            best_away_odds, best_away_book = price, key
                        if is_pinnacle:
                            pin_away = price

        if best_home_odds is None or best_away_odds is None:
            continue

        h_imp = american_to_prob(best_home_odds)
        a_imp = american_to_prob(best_away_odds)
        h_nv, a_nv = remove_vig(h_imp, a_imp)

        rows.append({
            "game_id":           game["id"],
            "commence_time":     game["commence_time"],
            "home_team":         home,
            "away_team":         away,
            "best_home_odds":    best_home_odds,
            "best_away_odds":    best_away_odds,
            "best_home_book":    best_home_book,
            "best_away_book":    best_away_book,
            "home_implied_prob": h_imp,
            "away_implied_prob": a_imp,
            "home_no_vig_prob":  h_nv,
            "away_no_vig_prob":  a_nv,
            "pinnacle_home":     pin_home,
            "pinnacle_away":     pin_away,
            "vig":               (h_imp + a_imp) - 1.0,
            "books_home":        books_home,
            "books_away":        books_away,
        })

    return pd.DataFrame(rows)
