"""
Generate daily picks narrative from the daily JSON output.

Produces a fully automated markdown briefing with:
- Season record tracker (W-L, P&L, ROI, bankroll)
- Pick narratives (5-6 sentences each with pitcher context, Elo, sim breakdown)
- Games We're Watching section for near-miss edges
- Footer with methodology

Usage:
    python scripts/generate_narrative.py                    # today
    python scripts/generate_narrative.py --date 2026-03-13  # specific date
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATA_DIR
from src.betting.odds import prob_to_american

DAILY_DIR = DATA_DIR / "daily"
RESULTS_PATH = DAILY_DIR / "results.json"


def _season_record():
    """Compute cumulative record and P&L from results.json."""
    if not RESULTS_PATH.exists():
        return {"wins": 0, "losses": 0, "pushes": 0, "profit": 0.0,
                "bankroll": 1000.0, "days": 0, "bets": 0}

    with open(RESULTS_PATH) as f:
        results = json.load(f)

    wins = sum(d["wins"] for d in results)
    losses = sum(d["losses"] for d in results)
    pushes = sum(d.get("pushes", 0) for d in results)
    profit = sum(d["day_profit"] for d in results)
    bankroll = results[-1]["bankroll"] if results else 1000.0
    bets = sum(d["picks_count"] for d in results)
    days = len(results)

    return {"wins": wins, "losses": losses, "pushes": pushes,
            "profit": profit, "bankroll": bankroll, "days": days, "bets": bets}


def _fmt_american(odds_str):
    """Format American odds string for display."""
    return odds_str if odds_str.startswith(("+", "-")) else f"+{odds_str}"


def _model_line(prob):
    """Convert model probability to American odds string."""
    odds = prob_to_american(prob)
    return f"{odds:+.0f}"


def _pick_narrative(pick, game):
    """Generate a 5-6 sentence narrative for a pick."""
    team = pick["team"]
    opponent = pick["opponent"]
    side = pick["side"]
    odds_str = _fmt_american(pick["odds"])
    edge = pick["edge_pct"]
    pick_type = pick.get("type", "moneyline")

    if pick_type != "moneyline" or game is None:
        return pick.get("explanation", "")

    model_prob = game["model_home_wp"] if side == "home" else game["model_away_wp"]
    market_prob = game.get("market_home_wp", 0.5) if side == "home" else game.get("market_away_wp", 0.5)
    sim_prob = game.get("sim_home_wp", 0.5) if side == "home" else 1 - game.get("sim_home_wp", 0.5)
    elo_prob = game.get("elo_home_wp", 0.5) if side == "home" else 1 - game.get("elo_home_wp", 0.5)
    fair_line = _model_line(model_prob)

    home_sp = game.get("home_pitcher", "TBD")
    away_sp = game.get("away_pitcher", "TBD")
    our_sp = home_sp if side == "home" else away_sp
    their_sp = away_sp if side == "home" else home_sp
    avg_runs = game.get("avg_total_runs", 0)

    lines = []

    # Sentence 1: The mispricing hook
    if market_prob < 0.5:
        lines.append(
            f"The books have {team} as an underdog at {odds_str} and we think that's wrong. "
            f"Our model puts them at {model_prob:.0%} to win — the fair line should be closer "
            f"to {fair_line}, not {odds_str}."
        )
    else:
        implied_line = _model_line(market_prob)
        lines.append(
            f"The market has {team} at {implied_line} but we think the gap should be wider. "
            f"Our model puts them at {model_prob:.0%} — the fair line is {fair_line}, "
            f"giving us a {edge:.1f}% edge."
        )

    # Sentence 2-3: What's driving the edge (Elo vs sim breakdown)
    elo_delta = elo_prob - sim_prob
    if abs(elo_delta) > 0.05:
        if elo_delta > 0:
            lines.append(
                f"The Elo team-strength gap is doing the heavy lifting here — "
                f"it has {team} at {elo_prob:.0%} while the pure pitching simulation "
                f"is more conservative at {sim_prob:.0%}."
            )
        else:
            lines.append(
                f"The pitching matchup is the driver here — our simulation has {team} "
                f"at {sim_prob:.0%} based on the {our_sp} vs {their_sp} matchup, "
                f"even though the Elo ratings are tighter at {elo_prob:.0%}."
            )
    else:
        lines.append(
            f"Both signals agree: the simulation has {team} at {sim_prob:.0%} and "
            f"Elo confirms at {elo_prob:.0%}. When the matchup-level model and the "
            f"team-strength model are aligned, we have higher conviction."
        )

    # Sentence 3-4: Pitching matchup context
    if side == "home":
        lines.append(
            f"On the mound, {our_sp} gets the ball at home against {their_sp} for "
            f"the visitors."
        )
    else:
        lines.append(
            f"On the mound, {our_sp} gets the ball on the road against {their_sp}."
        )

    # Sentence 5: The value angle
    if "+" in odds_str:
        lines.append(
            f"Getting plus money on a team our model favors is exactly the kind of "
            f"spot we're looking for."
        )
    else:
        lines.append(
            f"The price is steep but our model says it's not steep enough — "
            f"the {edge:.1f}% edge clears our threshold."
        )

    # Sentence 6: Run environment
    if avg_runs > 9.5:
        lines.append(f"This projects as a high-scoring game at {avg_runs:.1f} expected total runs.")
    elif avg_runs < 8.0:
        lines.append(f"This looks like a pitchers' duel — {avg_runs:.1f} expected total runs.")

    return " ".join(lines)


def _watching_narrative(game):
    """Generate a 2-3 sentence note for a non-pick game."""
    home = game["home"]
    away = game["away"]
    model_home = game["model_home_wp"]
    market_home = game.get("market_home_wp")
    sim_home = game.get("sim_home_wp", 0.5)
    elo_home = game.get("elo_home_wp", 0.5)
    home_sp = game.get("home_pitcher", "TBD")
    away_sp = game.get("away_pitcher", "TBD")
    avg_runs = game.get("avg_total_runs", 0)

    fav = home if model_home > 0.5 else away
    fav_prob = max(model_home, 1 - model_home)

    if market_home is None:
        return f"No odds available. Model has {fav} at {fav_prob:.0%}."

    market_fav = home if market_home > 0.5 else away
    market_prob = max(market_home, 1 - market_home)
    edge = fav_prob - (market_home if fav == home else 1 - market_home)

    lines = []

    if edge > 0.05:
        lines.append(
            f"We like {fav} here at {fav_prob:.0%} but the market already has "
            f"{market_fav} at {market_prob:.0%}. The {edge:.0%} edge after shrinkage "
            f"falls just short of our 7% threshold."
        )
    elif edge > 0.02:
        lines.append(
            f"Model has {fav} at {fav_prob:.0%}, market says {market_fav} at {market_prob:.0%}. "
            f"Directionally interesting but the edge is too thin to bet."
        )
    elif fav != market_fav:
        lines.append(
            f"The model disagrees with the market here — we have {fav} at {fav_prob:.0%} "
            f"but the books favor {market_fav}. Not enough conviction to take a position."
        )
    else:
        lines.append(
            f"Model and market agree on {fav}. No edge — the books priced this one right."
        )

    # Add pitcher color
    if home_sp != "TBD" and away_sp != "TBD":
        lines.append(f"{away_sp} vs {home_sp} on the mound.")

    return " ".join(lines)


def generate_narrative(date: str = None):
    date = date or datetime.now().strftime("%Y-%m-%d")
    picks_path = DAILY_DIR / f"{date}.json"

    if not picks_path.exists():
        print(f"No picks file for {date}. Run run_daily.py first.")
        return

    with open(picks_path) as f:
        data = json.load(f)

    games = data.get("games", [])
    picks = data.get("picks", [])
    bankroll_info = data.get("bankroll", {})
    record = _season_record()

    # --- Header ---
    dt = datetime.strptime(date, "%Y-%m-%d")
    day_name = dt.strftime("%A")
    month_day = dt.strftime("%B %d").replace(" 0", " ")

    print(f"# {day_name}, {month_day} — Daily Picks")
    print()

    # --- Record tracker ---
    total_bets = record["bets"]
    if total_bets > 0:
        w, l = record["wins"], record["losses"]
        bankroll = record["bankroll"]
        profit = record["profit"]
        starting = bankroll - profit
        roi = (profit / max(starting, 1)) * 100
        print(f"**Season Record:** {w}-{l}"
              + (f"-{record['pushes']}P" if record["pushes"] else "")
              + f" | P&L: ${profit:+,.2f}"
              + f" | Bankroll: ${bankroll:,.2f}"
              + f" | ROI: {roi:+.1f}%")
    else:
        bankroll = bankroll_info.get("current", 1000)
        print(f"**Season Record:** 0-0 | Bankroll: ${bankroll:,.2f} | First bets of the season!")
    print()

    # --- Summary line ---
    n_games = len(games)
    n_picks = len(picks)
    print(f"**{n_games} game{'s' if n_games != 1 else ''} on the board. "
          f"{n_picks} pick{'s' if n_picks != 1 else ''}.**")
    print()

    # --- Picks ---
    if picks:
        for pick in picks:
            team = pick["team"]
            opponent = pick["opponent"]
            side = pick["side"]
            odds_str = _fmt_american(pick["odds"])
            wager = pick.get("wager", 0)
            edge = pick["edge_pct"]
            pick_type = pick.get("type", "moneyline")

            # Find the matching game for context
            game = None
            for g in games:
                if (g["home"] == team and side == "home") or \
                   (g["away"] == team and side == "away"):
                    game = g
                    break

            if pick_type == "moneyline":
                model_prob = game["model_home_wp"] if side == "home" else game["model_away_wp"]
                market_prob = game.get("market_home_wp", 0.5) if side == "home" else game.get("market_away_wp", 0.5)
                fair_line = _model_line(model_prob)

                if side == "home":
                    matchup_str = f"{opponent} @ **{team}**"
                else:
                    matchup_str = f"**{team}** @ {opponent}"

                print(f"### {matchup_str}")
                print(f"**Pick: {team} ML ({odds_str}) — ${wager:.2f}**")
                print(f"Model: {model_prob:.0%} (fair line: {fair_line}) | "
                      f"Market: {market_prob:.0%} | Edge: {edge:.1f}%")
                print()
                print(_pick_narrative(pick, game))
                print()

            elif pick_type == "totals":
                print(f"### {pick['pick']}")
                print(f"**{pick['pick']} ({odds_str}) — ${wager:.2f}**")
                print(f"Edge: {edge:.1f}%")
                print()

    else:
        print("**No picks today.** Model didn't find any edges clearing the 7% threshold.")
        print()

    # --- Games we're watching ---
    non_pick_games = [g for g in games
                      if "pick" not in g and "totals_pick" not in g
                      and g.get("market_home_wp") is not None]
    if non_pick_games:
        print("---")
        print()
        print("### Games We're Watching")
        print()

        for g in non_pick_games:
            home = g["home"]
            away = g["away"]
            model_home = g["model_home_wp"]
            fav = home if model_home > 0.5 else away
            fav_prob = max(model_home, 1 - model_home)

            print(f"**{away} @ {home}** — Model: {fav} {fav_prob:.0%}")
            print(f": {_watching_narrative(g)}")
            print()

    # --- Footer ---
    print("---")
    print()
    print("*10,000 Monte Carlo sims per game. Quarter-Kelly sizing, 5% bankroll cap. "
          "We only bet when the edge clears 7%.*")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate daily picks narrative")
    parser.add_argument("--date", type=str, default=None)
    args = parser.parse_args()
    generate_narrative(date=args.date)
