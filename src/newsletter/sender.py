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
    "Every pitch. Every at-bat. Every outcome. Simulated.",
    "The spreadsheet strikes back.",
    "Brought to you by math, not vibes.",
    "We ran the numbers. Literally all of them.",
    "ERA is overrated. We simulate plate appearances.",
    "No hot takes. Just hot math.",
    "While you slept, we simulated 10,000 games.",
    "The algorithm ate its Wheaties today.",
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
    bankroll = round(10000.0 + total_profit, 2)

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
            f"Flawless. {wins}-0. The algorithm woke up and chose violence.",
            f"{wins} picks, {wins} wins, zero stress. Okay maybe some stress.",
        ]
    elif losses == total:
        blurbs = [
            f"Rough one — 0-{losses}. Variance giveth, variance taketh away.",
            f"The model went 0-{losses}. We don't talk about yesterday.",
            f"An 0-{losses} day. The math still maths, even when the results don't.",
            f"0-{losses}. Baseball is cruel sometimes. The model doesn't flinch.",
            f"Yesterday was pain. 0-{losses}. But one bad day doesn't break a model built on 10,000 sims.",
        ]
    elif profit > 0:
        blurbs = [
            f"Went {wins}-{losses} yesterday for +${profit:.0f}. The winners hit harder than the losses stung.",
            f"{wins}-{losses} on the day, banking +${profit:.0f}. Underdogs paid rent.",
            f"A {wins}-{losses} day that netted +${profit:.0f}. We'll take it.",
            f"Cashed +${profit:.0f} yesterday going {wins}-{losses}. Not every pick needs to hit when the math is on your side.",
            f"{wins} wins, {losses} losses, +${profit:.0f} in the bankroll. The model keeps grinding.",
        ]
    else:
        blurbs = [
            f"Went {wins}-{losses} for -${abs(profit):.0f}. The right side of variance will come back around.",
            f"{wins}-{losses} yesterday, down ${abs(profit):.0f}. Long season. Short memory.",
            f"A {wins}-{losses} day at -${abs(profit):.0f}. Shake it off — 10,000 sims don't lie over time.",
            f"Down ${abs(profit):.0f} yesterday ({wins}-{losses}). Some days the ball doesn't bounce your way. The edge is still real.",
            f"{wins}-{losses} for -${abs(profit):.0f}. Baseball has 162 games for a reason. We play the long game.",
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
            f"The simulation ran through every game and found one worth firing on: {team} at {odds}.",
        ]
    elif len(underdogs) == count:
        blurbs = [
            f"{count} underdogs on the card. The model loves a good plus-money play.",
            f"All {count} picks are dogs today. The simulation sees value where the market doesn't.",
            f"{count} plus-money plays today. The model is feeling spicy.",
            f"Dogs only today. {count} underdogs the market is sleeping on.",
        ]
    elif count >= 6:
        blurbs = [
            f"Loaded slate — {count} edges today with up to {max_edge:.1f}% edge. The model found a buffet.",
            f"Big day ahead. {count} picks on the board. The sims are feasting.",
            f"The algorithm went shopping and came back with {count} plays. Let's eat.",
        ]
    else:
        blurbs = [
            f"{count} picks today with edges up to {max_edge:.1f}%. Let the simulations ride.",
            f"The model found {count} edges worth playing. Here's what 10,000 sims say.",
            f"{count} plays on the board. The Monte Carlo engine has spoken.",
            f"Today's lineup: {count} picks, {max_edge:.1f}% max edge. The math checks out.",
            f"The sim crunched every matchup and landed on {count} plays. Here's the rundown.",
        ]

    return random.choice(blurbs)


def _enrich_pick_context(pick: dict, games: list) -> str:
    """Generate a rich, specific context blurb for a pick using game data."""
    team = pick.get("team", "")
    opponent = pick.get("opponent", "")

    # Find the matching game
    game = None
    for g in games:
        if (g.get("away") == team and g.get("home") == opponent) or \
           (g.get("home") == team and g.get("away") == opponent):
            game = g
            break

    if not game:
        return pick.get("explanation", "")

    parts = []
    weather = game.get("weather", {})
    park = game.get("park_factors", {})
    sim = game.get("sim_detail", {})
    elo_home = game.get("elo_home_rating", 0)
    elo_away = game.get("elo_away_rating", 0)
    home = game.get("home", "")
    away = game.get("away", "")
    is_home = team == home

    # Weather flavor
    temp = weather.get("temperature")
    condition = weather.get("condition", "")
    wind_speed = weather.get("wind_speed", 0)
    wind_dir = weather.get("wind_direction", "")
    if condition == "Dome":
        parts.append("Playing under the dome — controlled conditions.")
    elif temp:
        if temp < 45:
            parts.append(f"Bundle up — it'll be a frigid {temp:.0f}°F at first pitch.")
        elif temp > 85:
            parts.append(f"It's {temp:.0f}°F out there — the ball carries in the heat.")
        elif temp:
            parts.append(f"Game-time temp: {temp:.0f}°F.")
        if wind_speed and wind_speed > 10:
            if wind_dir == "out":
                parts.append(f"Wind blowing out at {wind_speed:.0f} mph — watch for the long ball.")
            elif wind_dir == "in":
                parts.append(f"Wind blowing in at {wind_speed:.0f} mph — pitchers' day.")

    # Park factor flavor
    hr_factor = park.get("HR", 1.0)
    runs_factor = park.get("runs", 1.0)
    if hr_factor > 1.1:
        parts.append("This park is a bandbox for homers.")
    elif hr_factor < 0.88:
        parts.append("Pitcher's park — homers go to die here.")

    # Elo gap
    if elo_home and elo_away:
        gap = abs(elo_home - elo_away)
        stronger = home if elo_home > elo_away else away
        if gap > 80:
            if stronger == team:
                parts.append(f"{team} holds a significant Elo edge ({gap} points) in this one.")
            else:
                parts.append(f"The Elo ratings favor {stronger} by {gap} points, but the sim sees it differently.")

    # Pitching matchup
    home_pitcher = game.get("home_pitcher", "")
    away_pitcher = game.get("away_pitcher", "")
    our_pitcher = home_pitcher if is_home else away_pitcher
    their_pitcher = away_pitcher if is_home else home_pitcher
    if our_pitcher:
        parts.append(f"{our_pitcher} takes the mound for {team} against {their_pitcher}.")

    # Sim run projection
    avg_home = sim.get("avg_home_runs", 0)
    avg_away = sim.get("avg_away_runs", 0)
    our_runs = avg_home if is_home else avg_away
    their_runs = avg_away if is_home else avg_home
    if our_runs and their_runs:
        parts.append(f"The sim projects {team} to score {our_runs:.1f} runs vs {opponent}'s {their_runs:.1f}.")

    # Limit to 3-4 sentences for readability
    return " ".join(parts[:4])


def _enrich_narrative_from_boxscore(pick: dict, date: str) -> str:
    """Pull actual player stats from boxscore for richer narratives."""
    team = pick.get("pick", "").replace(" ML", "").replace(" +1.5", "")
    game_id = _find_game_id(date, team)
    if not game_id:
        return pick.get("narrative", "")

    try:
        import statsapi
        box = statsapi.boxscore_data(game_id)
    except Exception:
        return pick.get("narrative", "")

    won = pick.get("won", False)
    parts = []

    # Find our team's batters
    team_key = None
    for key in ("home", "away"):
        if _normalize_abbr(team) == box.get(f"{key}Batting", {}).get("team", {}).get("abbreviation", ""):
            team_key = key
            break
        # Also check team name fragments
        abbr_map = {
            "ARI": "AZ", "ATL": "ATL", "BAL": "BAL", "BOS": "BOS",
            "CHC": "CHC", "CHW": "CWS", "CIN": "CIN", "CLE": "CLE",
            "COL": "COL", "DET": "DET", "HOU": "HOU", "KCR": "KC",
            "LAA": "LAA", "LAD": "LAD", "MIA": "MIA", "MIL": "MIL",
            "MIN": "MIN", "NYM": "NYM", "NYY": "NYY", "OAK": "OAK",
            "PHI": "PHI", "PIT": "PIT", "SDP": "SD", "SEA": "SEA",
            "SFG": "SF", "STL": "STL", "TBR": "TB", "TEX": "TEX",
            "TOR": "TOR", "WSN": "WSH",
        }
        norm = abbr_map.get(team, team)
        if norm == box.get(f"{key}Batting", {}).get("team", {}).get("abbreviation", ""):
            team_key = key
            break

    if not team_key:
        # Try simpler approach
        for key in ("home", "away"):
            batters = box.get(f"{key}Batters", [])
            if batters:
                team_key = key
                break

    if not team_key:
        return pick.get("narrative", "")

    # Get batting stats — find standout performers
    our_key = team_key
    opp_key = "away" if team_key == "home" else "home"

    try:
        our_batters = box.get(f"{our_key}Batters", [])
        opp_batters = box.get(f"{opp_key}Batters", [])

        # Find heroes/goats from stat lines
        for batter_id in our_batters:
            if isinstance(batter_id, int):
                stats = box.get(f"{our_key}BattingStats", {}).get(str(batter_id), {})
                name = box.get(f"{our_key}BattingNames", {}).get(str(batter_id), "")
                if not stats or not name:
                    continue
                hits = stats.get("h", 0)
                ab = stats.get("ab", 0)
                hr = stats.get("hr", 0)
                rbi = stats.get("rbi", 0)
                r = stats.get("r", 0)

                last_name = name.split()[-1] if name else "?"

                if hr >= 2:
                    parts.append(f"{last_name} went yard TWICE ({hits}-{ab}, {rbi} RBI).")
                elif hr == 1 and rbi >= 3:
                    parts.append(f"{last_name} crushed a homer and drove in {rbi} ({hits}-{ab}).")
                elif hr == 1:
                    parts.append(f"{last_name} left the yard ({hits}-{ab}, {rbi} RBI).")
                elif hits >= 3 and ab <= 5:
                    parts.append(f"{last_name} was on fire — {hits}-for-{ab} at the dish.")
                elif rbi >= 3:
                    parts.append(f"{last_name} drove in {rbi} runs ({hits}-{ab}).")

                if len(parts) >= 2:
                    break

        # If losing, mention who beat us
        if not won:
            for batter_id in opp_batters:
                if isinstance(batter_id, int):
                    stats = box.get(f"{opp_key}BattingStats", {}).get(str(batter_id), {})
                    name = box.get(f"{opp_key}BattingNames", {}).get(str(batter_id), "")
                    if not stats or not name:
                        continue
                    hr = stats.get("hr", 0)
                    rbi = stats.get("rbi", 0)
                    hits = stats.get("h", 0)
                    ab = stats.get("ab", 0)
                    last_name = name.split()[-1] if name else "?"

                    if hr >= 1 or rbi >= 3 or (hits >= 3 and ab <= 5):
                        desc = ""
                        if hr >= 1:
                            desc = f"{last_name} did the damage — {hits}-{ab} with {hr} HR, {rbi} RBI."
                        elif rbi >= 3:
                            desc = f"{last_name} drove in {rbi} against us ({hits}-{ab})."
                        elif hits >= 3:
                            desc = f"{last_name} was a thorn — {hits}-for-{ab}."
                        if desc:
                            parts.append(desc)
                            break

    except Exception:
        pass

    # Combine boxscore narrative with play-by-play narrative
    existing = pick.get("narrative", "")
    boxscore_text = " ".join(parts[:2])

    if boxscore_text and existing:
        return f"{boxscore_text} {existing}"
    return boxscore_text or existing


def render_email(picks_data: dict, season_stats: dict = None,
                 yesterday: dict = None) -> str:
    """Render the daily picks email as HTML."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    template = env.get_template("daily_email.html")

    tagline = random.choice(TAGLINES)
    recap_blurb = generate_recap_blurb(yesterday) if yesterday else ""
    picks_blurb = generate_picks_blurb(picks_data)

    # Enrich today's picks with game-specific context
    games = picks_data.get("games", [])
    for pick in picks_data.get("picks", []):
        pick["rich_context"] = _enrich_pick_context(pick, games)

    # Enrich yesterday's narratives with boxscore stats
    if yesterday:
        date = yesterday.get("date", "")
        for pick in yesterday.get("picks", []):
            if not pick.get("narrative") or len(pick.get("narrative", "")) < 10:
                pick["narrative"] = _enrich_narrative_from_boxscore(pick, date)

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
    import time
    audience_id = os.getenv("RESEND_AUDIENCE_ID", "")
    subscribers = []
    if audience_id:
        for attempt in range(3):
            try:
                contacts = resend.Contacts.list(audience_id=audience_id)
                if hasattr(contacts, 'get'):
                    contact_list = contacts.get('data', [])
                elif hasattr(contacts, 'data'):
                    contact_list = contacts.data
                else:
                    contact_list = list(contacts) if contacts else []
                subscribers = [c['email'] for c in contact_list if not c.get('unsubscribed')]
                if subscribers:
                    break
            except Exception as e:
                print(f"  Warning: Resend audience fetch attempt {attempt + 1} failed: {e}")
                time.sleep(2)
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

    import time
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
            time.sleep(0.3)  # Stay under Resend's 5 req/sec rate limit
        except Exception as e:
            print(f"    Failed to send to {email}: {e}")

    print(f"  Done. {len(subscribers)} emails sent.")
