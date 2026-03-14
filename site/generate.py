"""
Static site generator: reads daily picks JSON files, renders HTML via Jinja2.

Usage:
    python site/generate.py                  # generate full site
    python site/generate.py --date 2026-04-01  # regenerate specific day
"""

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from jinja2 import Environment, FileSystemLoader

SITE_DIR = Path(__file__).parent
TEMPLATE_DIR = SITE_DIR / "templates"
OUTPUT_DIR = SITE_DIR / "public"
DAILY_DIR = Path(__file__).parent.parent / "data" / "daily"
RESULTS_PATH = DAILY_DIR / "results.json"


def generate_site():
    """Generate the full static site."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    # Load all daily picks
    daily_files = sorted(DAILY_DIR.glob("*.json"))
    all_days = []
    for f in daily_files:
        if f.name == "results.json":
            continue
        with open(f) as fh:
            all_days.append(json.load(fh))

    # Load season results
    season_results = []
    if RESULTS_PATH.exists():
        with open(RESULTS_PATH) as f:
            season_results = json.load(f)

    # Compute season stats
    stats = _compute_season_stats(season_results)

    # Latest day's picks (for homepage)
    latest = all_days[-1] if all_days else None

    # --- Render pages ---
    # Index (today's picks)
    template = env.get_template("index.html")
    html = template.render(
        today=latest,
        stats=stats,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M ET"),
    )
    (OUTPUT_DIR / "index.html").write_text(html)

    # History
    template = env.get_template("history.html")
    html = template.render(
        results=season_results,
        stats=stats,
        all_days=all_days,
    )
    (OUTPUT_DIR / "history.html").write_text(html)

    # About
    template = env.get_template("about.html")
    html = template.render(stats=stats)
    (OUTPUT_DIR / "about.html").write_text(html)

    # Copy static assets
    _copy_static()

    print(f"Site generated: {OUTPUT_DIR}")
    print(f"  index.html, history.html, about.html")
    print(f"  {len(all_days)} daily pick files processed")


def _compute_season_stats(results):
    """Compute aggregate season statistics."""
    if not results:
        return {
            "total_picks": 0, "wins": 0, "losses": 0, "pushes": 0,
            "win_rate": 0, "total_profit": 0, "roi": 0,
            "current_bankroll": 10000, "starting_bankroll": 10000,
            "best_day": 0, "worst_day": 0, "current_streak": 0,
            "daily_pnl": [],
        }

    total_picks = sum(d["picks_count"] for d in results)
    wins = sum(d["wins"] for d in results)
    losses = sum(d["losses"] for d in results)
    pushes = sum(d.get("pushes", 0) for d in results)
    total_profit = sum(d["day_profit"] for d in results)
    total_wagered = sum(
        sum(p.get("wager", 0) for p in d.get("picks", []))
        for d in results
    )

    current_bankroll = results[-1]["bankroll"] if results else 10000
    starting_bankroll = results[0]["bankroll"] - results[0]["day_profit"] if results else 10000

    daily_pnl = []
    cumulative = 0
    for d in results:
        cumulative += d["day_profit"]
        daily_pnl.append({
            "date": d["date"],
            "day_profit": d["day_profit"],
            "cumulative": round(cumulative, 2),
            "bankroll": d["bankroll"],
        })

    # Streak
    streak = 0
    if results:
        for d in reversed(results):
            if d["day_profit"] > 0:
                streak += 1
            elif d["day_profit"] < 0:
                streak -= 1
                break
            else:
                break

    return {
        "total_picks": total_picks,
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "win_rate": round(wins / max(1, wins + losses) * 100, 1),
        "total_profit": round(total_profit, 2),
        "roi": round(total_profit / max(1, total_wagered) * 100, 1) if total_wagered else 0,
        "current_bankroll": round(current_bankroll, 2),
        "starting_bankroll": round(starting_bankroll, 2),
        "best_day": round(max((d["day_profit"] for d in results), default=0), 2),
        "worst_day": round(min((d["day_profit"] for d in results), default=0), 2),
        "current_streak": streak,
        "daily_pnl": daily_pnl,
    }


def _copy_static():
    """Copy static files to output directory."""
    static_dir = SITE_DIR / "static"
    out_static = OUTPUT_DIR / "static"
    out_static.mkdir(exist_ok=True)

    for f in static_dir.glob("*"):
        (out_static / f.name).write_bytes(f.read_bytes())


if __name__ == "__main__":
    generate_site()
