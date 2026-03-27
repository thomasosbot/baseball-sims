"""
Daily newsletter sender via Resend API.

Usage:
    from src.newsletter.sender import send_daily_picks
    send_daily_picks(picks_data)

Requires RESEND_API_KEY in .env.
Free tier: 100 emails/day, 3K/month.
"""

from __future__ import annotations

import json
import os
import random
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

TEMPLATE_DIR = Path(__file__).parent / "templates"
SUBSCRIBERS_PATH = Path(__file__).parent.parent.parent / "data" / "subscribers.json"
RESULTS_PATH = Path(__file__).parent.parent.parent / "data" / "daily" / "results.json"

# Taglines rotated daily
TAGLINES = [
    "10,000 simulations. One email. Zero gut feelings.",
    "Where Monte Carlo meets Moneyball.",
    "The algorithm doesn't have a favorite team.",
    "Statistically significant. Emotionally detached.",
    "We let the sims do the talking.",
    "Math doesn't care about your parlay.",
    "Fueled by Statcast. Unimpressed by narratives.",
    "Your daily dose of cold, hard probability.",
    "The model has no feelings to hurt.",
    "Simulated 10,000 times so you don't have to.",
]


def load_subscribers() -> list:
    """Load subscriber email list."""
    if not SUBSCRIBERS_PATH.exists():
        return []
    with open(SUBSCRIBERS_PATH) as f:
        data = json.load(f)
    return [s["email"] for s in data if s.get("active", True)]


def add_subscriber(email: str, name: str = ""):
    """Add a subscriber to the list."""
    subs = []
    if SUBSCRIBERS_PATH.exists():
        with open(SUBSCRIBERS_PATH) as f:
            subs = json.load(f)

    # Check for duplicates
    if any(s["email"] == email for s in subs):
        print(f"  {email} already subscribed")
        return

    subs.append({
        "email": email,
        "name": name,
        "active": True,
        "added": datetime.now().isoformat(),
    })

    SUBSCRIBERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SUBSCRIBERS_PATH, "w") as f:
        json.dump(subs, f, indent=2)
    print(f"  Added {email}")


def remove_subscriber(email: str):
    """Deactivate a subscriber."""
    if not SUBSCRIBERS_PATH.exists():
        return
    with open(SUBSCRIBERS_PATH) as f:
        subs = json.load(f)
    for s in subs:
        if s["email"] == email:
            s["active"] = False
    with open(SUBSCRIBERS_PATH, "w") as f:
        json.dump(subs, f, indent=2)
    print(f"  Deactivated {email}")


def load_yesterday_results() -> dict | None:
    """Load the most recent day's results from results.json."""
    if not RESULTS_PATH.exists():
        return None
    with open(RESULTS_PATH) as f:
        results = json.load(f)
    if not results:
        return None
    day = results[-1]
    # Enrich picks with game narratives if not already present
    if day.get("picks") and not day["picks"][0].get("narrative"):
        enrich_results_with_narratives(day)
    return day


def _find_game_id(date: str, team_abbr: str) -> int | None:
    """Find the MLB game ID for a team on a given date."""
    try:
        import statsapi
        sched = statsapi.schedule(date=date)
        # Map common abbreviations to team name fragments
        abbr_map = {
            "ARI": "Diamondbacks", "ATL": "Braves", "BAL": "Orioles",
            "BOS": "Red Sox", "CHC": "Cubs", "CHW": "White Sox",
            "CIN": "Reds", "CLE": "Guardians", "COL": "Rockies",
            "DET": "Tigers", "HOU": "Astros", "KCR": "Royals",
            "LAA": "Angels", "LAD": "Dodgers", "MIA": "Marlins",
            "MIL": "Brewers", "MIN": "Twins", "NYM": "Mets",
            "NYY": "Yankees", "OAK": "Athletics", "PHI": "Phillies",
            "PIT": "Pirates", "SDP": "Padres", "SEA": "Mariners",
            "SFG": "Giants", "STL": "Cardinals", "TBR": "Rays",
            "TEX": "Rangers", "TOR": "Blue Jays", "WSN": "Nationals",
        }
        team_name = abbr_map.get(team_abbr, team_abbr)
        for g in sched:
            if team_name in g.get("away_name", "") or team_name in g.get("home_name", ""):
                return g["game_id"]
    except Exception:
        pass
    return None


def _build_game_narrative(game_id: int, pick: dict) -> str:
    """Build a short, exciting narrative from play-by-play data."""
    try:
        import statsapi
        game_data = statsapi.get("game", {"gamePk": game_id})
        live = game_data.get("liveData", {})
        pbp = statsapi.get("game_playByPlay", {"gamePk": game_id})
    except Exception:
        return ""

    plays = pbp.get("allPlays", [])
    if not plays:
        return ""

    won = pick.get("won", False)
    score = pick.get("actual_score", "")
    team = pick.get("pick", "").replace(" ML", "")

    # Extract key moments
    scoring_plays = [p for p in plays if p.get("about", {}).get("isScoringPlay")]
    home_runs = [p for p in plays if p.get("result", {}).get("event") == "Home Run"]
    late_scoring = [p for p in scoring_plays if p.get("about", {}).get("inning", 0) >= 7]

    # Decisions
    decisions = live.get("decisions", {})
    wp = decisions.get("winner", {}).get("fullName", "")
    lp = decisions.get("loser", {}).get("fullName", "")
    sv = decisions.get("save", {}).get("fullName", "")

    # Final score info
    linescore = live.get("linescore", {})
    innings = linescore.get("currentInning", 9)
    extras = innings > 9

    parts = []

    if won:
        # Find the hero moment
        team_hrs = [
            hr for hr in home_runs
            if _is_our_team_batting(hr, team, game_data)
        ]
        team_late_scores = [
            sp for sp in late_scoring
            if _is_our_team_batting(sp, team, game_data)
        ]

        if team_hrs:
            # Pick the most dramatic HR (latest inning)
            hero_hr = team_hrs[-1]
            batter = hero_hr.get("matchup", {}).get("batter", {}).get("fullName", "?").split()[-1]
            inning = hero_hr.get("about", {}).get("inning", 0)
            if inning >= 7:
                parts.append(f"{batter} went deep in the {_ordinal(inning)} to blow it open.")
            elif len(team_hrs) > 1:
                first = team_hrs[0].get("matchup", {}).get("batter", {}).get("fullName", "?").split()[-1]
                parts.append(f"{first} and {batter} both went yard.")
            else:
                parts.append(f"{batter} launched one out of the park.")

        if extras:
            parts.append(f"Took {innings} innings to settle this one.")
        elif not team_hrs and team_late_scores:
            last_play = team_late_scores[-1]
            batter = last_play.get("matchup", {}).get("batter", {}).get("fullName", "?").split()[-1]
            event = last_play.get("result", {}).get("event", "")
            inn = last_play.get("about", {}).get("inning", 9)
            parts.append(f"{batter} came through with a {_ordinal(inn)}-inning {event.lower()}.")

        if sv:
            parts.append(f"{sv} slammed the door.")
        elif wp and len(parts) < 2:
            parts.append(f"{wp} picked up the W on the mound.")

    else:
        # Loss narrative — find what beat us
        opp_hrs = [
            hr for hr in home_runs
            if not _is_our_team_batting(hr, team, game_data)
        ]

        if opp_hrs:
            killer_hr = opp_hrs[-1]
            batter = killer_hr.get("matchup", {}).get("batter", {}).get("fullName", "?").split()[-1]
            inning = killer_hr.get("about", {}).get("inning", 0)
            if len(opp_hrs) >= 2:
                parts.append(f"{batter} and company hit {len(opp_hrs)} homers. Hard to overcome that kind of power.")
            elif inning >= 7:
                parts.append(f"{batter} crushed a late homer in the {_ordinal(inning)}. That one stung.")
            else:
                parts.append(f"{batter} took us deep.")

        if extras:
            parts.append(f"Went {innings} innings but came up short.")
        elif not opp_hrs:
            parts.append(f"{wp} was too much on the mound." if wp else "Couldn't get the bats going.")

        # Look for a fighting moment on our side
        our_late = [sp for sp in late_scoring if _is_our_team_batting(sp, team, game_data)]
        if our_late and len(parts) < 2:
            parts.append("Rallied late but couldn't close the gap.")

    if not parts:
        if won:
            parts.append("Got the W. Model stays eating.")
        else:
            parts.append("Tough break. On to the next one.")

    return " ".join(parts[:2])


def _normalize_abbr(abbr: str) -> str:
    """Normalize team abbreviations between our data and the MLB API."""
    aliases = {
        "WSN": "WSH", "WSH": "WSH",
        "CHW": "CWS", "CWS": "CWS",
        "KCR": "KC", "KC": "KC",
        "SDP": "SD", "SD": "SD",
        "SFG": "SF", "SF": "SF",
        "TBR": "TB", "TB": "TB",
    }
    return aliases.get(abbr, abbr)


def _is_our_team_batting(play: dict, team_abbr: str, game_data: dict) -> bool:
    """Check if the batting team in a play matches our picked team."""
    half = play.get("about", {}).get("halfInning", "")
    game_info = game_data.get("gameData", {}).get("teams", {})
    away_abbr = game_info.get("away", {}).get("abbreviation", "")
    home_abbr = game_info.get("home", {}).get("abbreviation", "")
    norm = _normalize_abbr(team_abbr)
    if half == "top":
        return away_abbr == norm
    else:
        return home_abbr == norm


def _ordinal(n: int) -> str:
    """Convert integer to ordinal string (1st, 2nd, 3rd, etc.)."""
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def enrich_results_with_narratives(day_results: dict):
    """Add game narratives to each pick in a day's results."""
    date = day_results.get("date", "")
    for pick in day_results.get("picks", []):
        if pick.get("narrative"):
            continue
        team = pick.get("pick", "").replace(" ML", "")
        game_id = _find_game_id(date, team)
        if game_id:
            narrative = _build_game_narrative(game_id, pick)
            pick["narrative"] = narrative
        else:
            pick["narrative"] = ""


def build_season_stats(results: list) -> dict:
    """Compute season-level stats from the results log."""
    total_wins = sum(r.get("wins", 0) for r in results)
    total_losses = sum(r.get("losses", 0) for r in results)
    total_wagered = sum(
        sum(abs(p.get("wager", 0)) for p in r.get("picks", []))
        for r in results
    )
    total_profit = sum(r.get("day_profit", 0) for r in results)
    roi = round(total_profit / total_wagered * 100, 1) if total_wagered > 0 else 0
    bankroll = results[-1].get("bankroll", 10000) if results else 10000

    return {
        "wins": total_wins,
        "losses": total_losses,
        "total_profit": total_profit,
        "roi": roi,
        "bankroll": bankroll,
    }


def generate_recap_blurb(yesterday: dict) -> str:
    """Generate a witty recap of yesterday's results."""
    wins = yesterday.get("wins", 0)
    losses = yesterday.get("losses", 0)
    profit = yesterday.get("day_profit", 0)
    picks = yesterday.get("picks", [])
    total = wins + losses

    if total == 0:
        return "No picks yesterday — the model took a day off."

    if wins == total:
        blurbs = [
            f"Perfect {wins}-0 day. The simulation gods smiled upon us.",
            f"Swept the board — {wins} for {wins}. Don't get used to it.",
            f"Clean sweep yesterday. {wins}-0. The model was locked in.",
        ]
    elif losses == total:
        blurbs = [
            f"Rough one — 0-{losses}. Variance giveth, variance taketh away.",
            f"The model went 0-{losses}. We don't talk about yesterday.",
            f"An 0-{losses} day. The math still maths, even when the results don't.",
        ]
    elif profit > 0:
        blurbs = [
            f"Went {wins}-{losses} yesterday for +${profit:.0f}. The winners hit harder than the losses stung.",
            f"{wins}-{losses} on the day, banking +${profit:.0f}. Underdogs paid rent.",
            f"A {wins}-{losses} day that netted +${profit:.0f}. We'll take it.",
        ]
    else:
        blurbs = [
            f"Went {wins}-{losses} for -${abs(profit):.0f}. The right side of variance will come back around.",
            f"{wins}-{losses} yesterday, down ${abs(profit):.0f}. Long season. Short memory.",
            f"A {wins}-{losses} day at -${abs(profit):.0f}. Shake it off — 10,000 sims don't lie over time.",
        ]

    return random.choice(blurbs)


def generate_picks_blurb(picks_data: dict) -> str:
    """Generate a blurb introducing today's picks."""
    picks = picks_data.get("picks", [])
    count = len(picks)

    if count == 0:
        return ""

    underdogs = [p for p in picks if p.get("odds", "").startswith("+")]
    max_edge = max((p.get("edge_pct", 0) for p in picks), default=0)

    if count == 1:
        pick = picks[0]
        team = pick.get("team", "")
        odds = pick.get("odds", "")
        blurbs = [
            f"One play today. The model likes {team} at {odds} — quality over quantity.",
            f"Just one edge cleared the threshold today: {team} at {odds}. One bullet, make it count.",
            f"Slim pickings today — but {team} at {odds} has the model's attention.",
        ]
    elif len(underdogs) == count:
        blurbs = [
            f"{count} underdogs on the card. The model loves a good plus-money play.",
            f"All {count} picks are dogs today. The simulation sees value where the market doesn't.",
            f"{count} plus-money plays today. The model is feeling spicy.",
        ]
    else:
        blurbs = [
            f"{count} picks today with edges up to {max_edge:.1f}%. Let the simulations ride.",
            f"The model found {count} edges worth playing. Here's what 10,000 sims say.",
            f"{count} plays on the board. The Monte Carlo engine has spoken.",
        ]

    return random.choice(blurbs)


def render_email(picks_data: dict, season_stats: dict = None,
                 yesterday: dict = None) -> str:
    """Render the daily picks email as HTML."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("daily_email.html")

    tagline = random.choice(TAGLINES)
    recap_blurb = generate_recap_blurb(yesterday) if yesterday else ""
    picks_blurb = generate_picks_blurb(picks_data)

    return template.render(
        today=picks_data,
        yesterday=yesterday,
        stats=season_stats or {},
        date=picks_data.get("date", datetime.now().strftime("%Y-%m-%d")),
        tagline=tagline,
        recap_blurb=recap_blurb,
        picks_blurb=picks_blurb,
    )


def send_daily_picks(picks_data: dict, season_stats: dict = None):
    """
    Send daily picks email to all active subscribers.

    picks_data: the daily JSON output from run_daily.py
    season_stats: optional season summary stats
    """
    try:
        import resend
    except ImportError:
        print("  ERROR: resend package not installed. Run: pip install resend")
        return

    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        print("  ERROR: RESEND_API_KEY not set in .env")
        return

    resend.api_key = api_key

    # Load yesterday's results and season stats
    yesterday = load_yesterday_results()
    if season_stats is None:
        if RESULTS_PATH.exists():
            with open(RESULTS_PATH) as f:
                all_results = json.load(f)
            if all_results:
                season_stats = build_season_stats(all_results)

    # Try Resend audience first, fall back to local file
    audience_id = os.getenv("RESEND_AUDIENCE_ID", "")
    subscribers = []
    if audience_id:
        try:
            contacts = resend.Contacts.list(audience_id=audience_id)
            contact_list = contacts.get('data', []) if hasattr(contacts, 'get') else []
            subscribers = [c['email'] for c in contact_list if not c.get('unsubscribed')]
        except Exception as e:
            print(f"  Warning: could not fetch Resend audience: {e}")
    if not subscribers:
        subscribers = load_subscribers()
    if not subscribers:
        print("  No subscribers found.")
        return

    html = render_email(picks_data, season_stats, yesterday)
    date = picks_data.get("date", datetime.now().strftime("%Y-%m-%d"))
    picks_count = len(picks_data.get("picks", []))

    if picks_count > 0:
        subject = f"Ozzy Analytics — {date} — {picks_count} pick{'s' if picks_count != 1 else ''}"
    else:
        subject = f"Ozzy Analytics — {date} — No edges today"

    from_email = os.getenv("RESEND_FROM_EMAIL", "picks@ozzyanalytics.com")

    print(f"  Sending to {len(subscribers)} subscribers...")
    for email in subscribers:
        try:
            resend.Emails.send({
                "from": f"Ozzy Analytics <{from_email}>",
                "to": email,
                "subject": subject,
                "html": html,
            })
            print(f"    Sent to {email}")
        except Exception as e:
            print(f"    Failed to send to {email}: {e}")

    print(f"  Done. {len(subscribers)} emails sent.")
